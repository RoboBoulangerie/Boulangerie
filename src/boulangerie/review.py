"""Data for the "Review your files and tables" page.

Maintainers are the judges of whether we described their data correctly, so
they need to see every file and table, not just a sample. Items are ranked so
the ones most likely to be wrong come first:

1. items the checks found something wrong with (worst first)
2. items the pipeline was less sure about
3. items nobody has reviewed yet
"""

import re

from boulangerie import organize, paths
from boulangerie.checks import SEVERITY_ORDER, WARNING, Finding
from boulangerie.summary import format_name, human_size, parse_size

SURE = 0.9  # below this, an item is flagged for a look
NO_FINDING = len(SEVERITY_ORDER)


def item_of(path: str, croissant: dict) -> str | None:
    """The @id of the file or table a finding's path points into, e.g. distribution[3].name -> its @id."""
    parts = paths.parse(path)
    if len(parts) >= 2 and parts[0] in ("distribution", "recordSet") and isinstance(parts[1], int):
        items = croissant.get(parts[0]) or []
        if parts[1] < len(items):
            return items[parts[1]].get("@id")
    return None


def _type_name(data_type) -> str:
    if isinstance(data_type, list):
        return " / ".join(_type_name(t) for t in data_type)
    if not isinstance(data_type, str):
        return ""
    return data_type.split(":", 1)[-1]


def _attention(issues: list[dict], conf: dict | None) -> tuple[bool, tuple]:
    worst = min((SEVERITY_ORDER[i["severity"]] for i in issues), default=NO_FINDING)
    confidence = conf.get("confidence") if conf else None
    reviewed = bool(conf) and conf.get("values", 0) > 0 and conf.get("reviewed", 0) >= conf["values"]
    look = worst <= SEVERITY_ORDER[WARNING] or (confidence is not None and confidence < SURE)
    return look, (worst, confidence if confidence is not None else 1.0, reviewed)


def _issues_by_item(croissant: dict, findings: list[Finding]) -> dict[str, list[dict]]:
    by_item: dict[str, list[dict]] = {}
    seen: set[tuple] = set()
    for f in findings:
        item = item_of(f.path, croissant)
        if item is None or (item, f.check) in seen:
            continue
        seen.add((item, f.check))
        by_item.setdefault(item, []).append({"severity": f.severity, "title": f.title, "detail": f.technical})
    for issues in by_item.values():
        issues.sort(key=lambda i: SEVERITY_ORDER[i["severity"]])
    return by_item


# Plain words for the parts of a file or column the pipeline can be unsure about.
PART_NAMES = {
    "name": "name", "description": "description", "contentUrl": "download link", "encodingFormat": "file type",
    "contentSize": "size", "sha256": "checksum", "md5": "checksum", "dataType": "kind of value",
    "source.extract.column": "column in the file", "includes": "file pattern", "containedIn.@id": "archive",
}


def describe_part(path: str, fields: list[dict] | None = None) -> str:
    """'field[2].dataType' -> 'pos · kind of value'; 'encodingFormat' -> 'file type'."""
    m = re.match(r"^field\[(\d+)\](?:\.(.*))?$", path)
    if m and fields is not None and int(m.group(1)) < len(fields):
        column = fields[int(m.group(1))].get("name") or f"column {int(m.group(1)) + 1}"
        rest = m.group(2) or ""
        return f"{column} · {PART_NAMES.get(rest, rest)}" if rest else column
    return PART_NAMES.get(path, path) or "this item"


def _unsure(conf: dict | None, fields: list[dict] | None = None) -> list[dict]:
    return [{"part": describe_part(u["path"], fields), "value": _type_name(u["value"]) if str(u["value"]).startswith(("sc:", "cr:")) else u["value"],
             "confidence": u["confidence"]}
            for u in (conf or {}).get("unsure", [])]


def build_review(croissant: dict, confidence: dict[str, dict], findings: list[Finding]) -> dict:
    """Every file and table, with what we know about it, ranked most-likely-wrong first."""
    dists = croissant.get("distribution") or []
    names = {d.get("@id"): d.get("name") or d.get("contentUrl") or d.get("@id") for d in dists}
    issues = _issues_by_item(croissant, findings)

    files = []
    for order, d in enumerate(dists):
        item_id = d.get("@id")
        conf = confidence.get(item_id)
        item_issues = issues.get(item_id, [])
        look, rank = _attention(item_issues, conf)
        parent = d.get("containedIn")
        parent_id = parent.get("@id") if isinstance(parent, dict) else None
        url = d.get("contentUrl")
        size = parse_size(d.get("contentSize"))
        files.append({
            "id": item_id,
            "name": names[item_id],
            "kind": format_name(d.get("encodingFormat")),
            "size": human_size(size) if size is not None else "",
            "url": url if isinstance(url, str) and url.startswith(("http://", "https://", "ftp://")) else "",
            "inside": names.get(parent_id, "") if parent_id else "",
            "group": d.get("@type") == "cr:FileSet",
            "pattern": d.get("includes") if d.get("@type") == "cr:FileSet" else "",
            "description": d.get("description") or "",
            "confidence": conf.get("confidence") if conf else None,
            "unsure": _unsure(conf),
            "issues": item_issues,
            "look": look,
            "rank": rank,
            "order": order,
        })

    tables = []
    for order, rs in enumerate(croissant.get("recordSet") or []):
        item_id = rs.get("@id")
        conf = confidence.get(item_id)
        item_issues = issues.get(item_id, [])
        look, rank = _attention(item_issues, conf)
        tables.append({
            "id": item_id,
            "name": rs.get("name") or item_id,
            "description": rs.get("description") or "",
            "confidence": conf.get("confidence") if conf else None,
            "unsure": _unsure(conf, rs.get("field") or []),
            "issues": item_issues,
            "look": look,
            "rank": rank,
            "order": order,
        })

    # Lists stay in croissant order (the tree and table families point into them by position);
    # "rank" is each item's place in most-likely-wrong-first order.
    for items in (files, tables):
        for position, item in enumerate(sorted(items, key=lambda i: (i["rank"], i["order"]))):
            item["rank"] = position

    paths = organize.relative_paths(dists)
    for f in files:
        f["path"] = paths.get(f["id"], f["name"])
    tree = organize.build_file_tree(dists, [f["look"] for f in files])
    families = organize.build_table_families(croissant.get("recordSet") or [], paths, [t["look"] for t in tables])
    return {
        "files": files,
        "tables": tables,
        "tree": tree,
        "families": families,
        "counts": {
            "files": len(files), "tables": len(tables),
            "files_look": sum(i["look"] for i in files), "tables_look": sum(i["look"] for i in tables),
            "families": len(families), "families_look": sum(f["look"] > 0 for f in families),
        },
    }

