"""Import dataset-level provenance from the pipeline/dashboard database (``robo_croissant.db``).

The database's ``kb_links`` table has one row per value in each croissant
(path, value, source URL, confidence, review flags): millions of rows, almost
all describing individual files and columns. We keep only the dataset-level
rows (name, description, license, ...), which is what maintainers see.
"""

import sqlite3
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


def kb_names_in_db(db_path: Path) -> list[str]:
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
        return [name for (name,) in conn.execute("SELECT name FROM knowledge_bases ORDER BY name")]
