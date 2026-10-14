"""Import provenance and confidence from the pipeline/dashboard database (``robo_croissant.db``).

The database's ``kb_links`` table has one row per value in each croissant
(path, value, source URL, confidence, review flags): millions of rows, almost
all describing individual files and columns. We keep:

- every dataset-level row (name, description, license, ...) in ``provenance.json``
- one summary per file and per table in ``confidence.json``: how sure the
  pipeline was, how much has been reviewed, and its least certain values
"""

import fnmatch
import json
import re
import sqlite3
from collections import defaultdict
from pathlib import Path

# Paths under these prefixes describe individual files and columns.
FILE_AND_COLUMN_PREFIXES = ("distribution[", "recordSet[")


def is_dataset_level(path: str) -> bool:
    return not path.startswith(FILE_AND_COLUMN_PREFIXES) and not path.lstrip('"').startswith("@")


def read_dataset_provenance(db_path: Path, kb_name: str) -> dict[str, dict]:
    """Dataset-level provenance for one KB. Raises if the KB isn't in the database."""
    # Read-only, so we can never modify the pipeline's database.
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
        # `kb_name = ?` uses the (kb_name, path) primary key, so this doesn't scan all rows.
        # Filtering in SQL on the path keeps millions of file/column rows out of Python.
        rows = conn.execute(
            "SELECT path, value, url, confidence, reviewed, auto_reviewed FROM kb_links "
            "WHERE kb_name = ? AND path NOT LIKE 'distribution[%' AND path NOT LIKE 'recordSet[%'",
            (kb_name,),
        ).fetchall()
    if not rows:
        raise KeyError(f"{kb_name!r} has no rows in {db_path}")
    return {
        path: {
            "path": path,
            "value": value,
            "url": url or "",
            "confidence": confidence,
            "source": "pipeline",
            "reviewed": bool(reviewed),
            "auto_reviewed": bool(auto_reviewed),
        }
        for path, value, url, confidence, reviewed, auto_reviewed in rows
        if is_dataset_level(path)
    }


UNSURE_BELOW = 0.9  # same cutoff the review page uses to flag an item
MAX_UNSURE = 5
_ITEM = re.compile(r"^(distribution|recordSet)\[(\d+)\](?:\.(.*))?$")


def _summarize(rows: list[tuple]) -> dict:
    """rows: (rest of path, value, confidence, reviewed, auto_reviewed) for one file or table."""
    confidences = [c for _, _, c, _, _ in rows if c is not None]
    unsure = sorted((c, rest, value) for rest, value, c, _, _ in rows if c is not None and c < UNSURE_BELOW)
    return {
        "confidence": min(confidences) if confidences else None,
        "values": len(rows),
        "reviewed": sum(bool(r) for *_, r, _ in rows),
        "auto_reviewed": sum(bool(a) for *_, a in rows),
        "unsure": [{"path": rest, "value": value, "confidence": c} for c, rest, value in unsure[:MAX_UNSURE]],
    }


def read_item_confidence(db_path: Path, kb_name: str, current: dict) -> dict[str, dict]:
    """How sure the pipeline was about each file and table, keyed by the item's ``@id``.

    The database stores rows by position (``distribution[12].name``), so positions are mapped to ids using the
    database's own copy of the croissant. FileSets we created by collapsing archive members get the lowest
    confidence among their members. Only ids in the ``current`` croissant are kept.
    """
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
        (raw,) = conn.execute("SELECT croissant_metadata FROM knowledge_bases WHERE name = ?", (kb_name,)).fetchone()
        rows = conn.execute(
            "SELECT path, value, confidence, reviewed, auto_reviewed FROM kb_links "
            "WHERE kb_name = ? AND (path LIKE 'distribution[%' OR path LIKE 'recordSet[%')",
            (kb_name,),
        ).fetchall()
    stored = json.loads(raw)
    ids = {
        "distribution": [d.get("@id") for d in stored.get("distribution") or []],
        "recordSet": [r.get("@id") for r in stored.get("recordSet") or []],
    }
    by_id: dict[str, list[tuple]] = defaultdict(list)
    for path, value, confidence, reviewed, auto_reviewed in rows:
        m = _ITEM.match(path)
        if not m:
            continue
        kind, index, rest = m.group(1), int(m.group(2)), m.group(3) or ""
        if index >= len(ids[kind]):
            raise ValueError(f"{kb_name}: {path} is beyond the stored croissant's {kind} list")
        by_id[ids[kind][index]].append((rest, value, confidence, reviewed, auto_reviewed))

    current_ids = {d.get("@id") for d in current.get("distribution") or []} | {
        r.get("@id") for r in current.get("recordSet") or []}
    items = {item_id: _summarize(item_rows) for item_id, item_rows in by_id.items() if item_id in current_ids}

    # FileSets made by collapse_filesets: summarize their former members.
    stored_dists = stored.get("distribution") or []
    for fs in current.get("distribution") or []:
        if fs.get("@type") != "cr:FileSet" or fs.get("@id") in items:
            continue
        archive = (fs.get("containedIn") or {}).get("@id")
        pattern = fs.get("includes")
        if not archive or not isinstance(pattern, str):
            continue
        member_rows = [
            row
            for d in stored_dists
            if (d.get("containedIn") or {}).get("@id") == archive
            and isinstance(d.get("contentUrl"), str)
            and fnmatch.fnmatchcase(d["contentUrl"].split("!/", 1)[-1], pattern)
            for row in by_id.get(d.get("@id"), [])
        ]
        if member_rows:
            items[fs["@id"]] = _summarize(member_rows)
    return items


def kb_names_in_db(db_path: Path) -> list[str]:
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
        return [name for (name,) in conn.execute("SELECT name FROM knowledge_bases ORDER BY name")]
