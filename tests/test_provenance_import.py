import json
import sqlite3

import pytest

from boulangerie import provenance_import
from boulangerie.catalog import read_provenance
from boulangerie.cli import main


@pytest.fixture
def db(tmp_path):
    """A small database with the same schema as the pipeline's robo_croissant.db."""
    path = tmp_path / "robo_croissant.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE knowledge_bases (name TEXT PRIMARY KEY, url TEXT, croissant_metadata JSONB)")
        conn.execute("CREATE TABLE kb_links (kb_name TEXT, path TEXT, value TEXT, url TEXT, confidence REAL, "
                     "reviewed BOOLEAN, auto_reviewed BOOLEAN, PRIMARY KEY (kb_name, path))")
        conn.execute("INSERT INTO knowledge_bases VALUES ('Tiny KB', 'https://example.org/', '{}')")
        conn.executemany("INSERT INTO kb_links VALUES (?, ?, ?, ?, ?, ?, ?)", [
            ("Tiny KB", "name", "Tiny KB", "https://example.org/", 1.0, 1, 0),
            ("Tiny KB", "license", "Not Found", "https://example.org/data", 0.3, 0, 0),
            ("Tiny KB", "keywords[0]", "genes", "https://example.org/", 0.9, 0, 0),
            ("Tiny KB", '"@context".cr', "http://mlcommons.org/croissant/", "", 1.0, 0, 1),
            ("Tiny KB", "distribution[0].name", "genes.tsv", "https://example.org/genes.tsv", 1.0, 0, 1),
            ("Tiny KB", "recordSet[0].field[0].name", "symbol", "https://example.org/genes.tsv", 1.0, 0, 1),
            ("Other KB", "name", "Other", "https://other.org/", 1.0, 0, 0),
        ])
    return path


def test_reads_only_dataset_level_rows(db):
    provenance = provenance_import.read_dataset_provenance(db, "Tiny KB")
    assert set(provenance) == {"name", "license", "keywords[0]"}
    assert provenance["name"] == {
        "path": "name", "value": "Tiny KB", "url": "https://example.org/", "confidence": 1.0,
        "source": "pipeline", "reviewed": True, "auto_reviewed": False,
    }


def test_unknown_kb_fails_loudly(db):
    with pytest.raises(KeyError):
        provenance_import.read_dataset_provenance(db, "Nope")


def test_database_is_opened_read_only(db):
    before = db.read_bytes()
    provenance_import.read_dataset_provenance(db, "Tiny KB")
    assert db.read_bytes() == before


def test_cli_import_keeps_maintainer_confirmed_values(db, tiny_kb, monkeypatch):
    # The CLI loads the catalog from KBS_DIR; point it at the fixture copy.
    monkeypatch.setattr("boulangerie.cli.load_catalog", lambda: [tiny_kb])
    confirmed = {"path": "license", "value": "https://creativecommons.org/publicdomain/zero/1.0/",
                 "url": "https://github.com/o/r/issues/3", "confidence": 1.0, "source": "maintainer"}
    tiny_kb.provenance_path.write_text(json.dumps([confirmed]))

    main(["import-provenance", "--db", str(db)])

    provenance = read_provenance(tiny_kb)
    assert provenance["license"] == confirmed
    assert provenance["name"]["source"] == "pipeline"
    assert "description" not in provenance  # old dashboard rows are replaced, not merged
