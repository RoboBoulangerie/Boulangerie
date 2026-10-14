import json
import sqlite3

import pytest

from boulangerie import provenance_import
from boulangerie.catalog import read_provenance
from boulangerie.cli import main


# The database's own copy of the croissant: rows refer to files and tables by position in it.
STORED = {
    "distribution": [
        {"@id": "file-genes-tsv"}, {"@id": "file-readme"},
        {"@id": "m1", "containedIn": {"@id": "zip"}, "contentUrl": "a.zip!/a_1.txt"},
        {"@id": "m2", "containedIn": {"@id": "zip"}, "contentUrl": "a.zip!/a_2.txt"},
    ],
    "recordSet": [{"@id": "genes"}],
}


@pytest.fixture
def db(tmp_path):
    """A small database with the same schema as the pipeline's robo_croissant.db."""
    path = tmp_path / "robo_croissant.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE knowledge_bases (name TEXT PRIMARY KEY, url TEXT, croissant_metadata JSONB)")
        conn.execute("CREATE TABLE kb_links (kb_name TEXT, path TEXT, value TEXT, url TEXT, confidence REAL, "
                     "reviewed BOOLEAN, auto_reviewed BOOLEAN, PRIMARY KEY (kb_name, path))")
        conn.execute("INSERT INTO knowledge_bases VALUES ('Tiny KB', 'https://example.org/', ?)", (json.dumps(STORED),))
        conn.executemany("INSERT INTO kb_links VALUES (?, ?, ?, ?, ?, ?, ?)", [
            ("Tiny KB", "name", "Tiny KB", "https://example.org/", 1.0, 1, 0),
            ("Tiny KB", "license", "Not Found", "https://example.org/data", 0.3, 0, 0),
            ("Tiny KB", "keywords[0]", "genes", "https://example.org/", 0.9, 0, 0),
            ("Tiny KB", '"@context".cr', "http://mlcommons.org/croissant/", "", 1.0, 0, 1),
            ("Tiny KB", "distribution[0].name", "genes.tsv", "https://example.org/genes.tsv", 1.0, 0, 1),
            ("Tiny KB", "recordSet[0].field[0].name", "symbol", "https://example.org/genes.tsv", 1.0, 0, 1),
            ("Tiny KB", "recordSet[0].field[1].name", "where", "https://example.org/genes.tsv", 0.4, 0, 0),
            ("Tiny KB", "distribution[1].name", "README.pdf", "https://example.org/README.pdf", 0.95, 1, 0),
            ("Tiny KB", "distribution[2].name", "a_1.txt", "https://example.org/a.zip", 0.8, 0, 0),
            ("Tiny KB", "distribution[3].name", "a_2.txt", "https://example.org/a.zip", 0.6, 0, 0),
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


def test_item_confidence_is_keyed_by_id_and_summarized(db, tiny_croissant):
    items = provenance_import.read_item_confidence(db, "Tiny KB", tiny_croissant)
    assert set(items) == {"file-genes-tsv", "file-readme", "genes"}
    assert items["genes"]["confidence"] == 0.4
    assert items["genes"]["values"] == 2
    assert items["genes"]["unsure"] == [{"path": "field[1].name", "value": "where", "confidence": 0.4}]
    assert items["file-readme"]["reviewed"] == 1


def test_collapsed_filesets_summarize_their_members(db, tiny_croissant):
    tiny_croissant["distribution"].append({"@id": "zip/fileset-0", "@type": "cr:FileSet",
                                           "containedIn": {"@id": "zip"}, "includes": "a_*.txt"})
    items = provenance_import.read_item_confidence(db, "Tiny KB", tiny_croissant)
    assert items["zip/fileset-0"]["confidence"] == 0.6
    assert items["zip/fileset-0"]["values"] == 2
    assert "m1" not in items  # former members aren't in the current croissant
