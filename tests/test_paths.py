from boulangerie import paths


def test_parse_mixed_keys_and_indexes():
    assert paths.parse("recordSet[2].field[10].source.extract.column") == [
        "recordSet", 2, "field", 10, "source", "extract", "column"]


def test_get_and_set_round_trip():
    doc = {"distribution": [{"name": "a"}, {"name": "b"}]}
    assert paths.get(doc, "distribution[1].name") == "b"
    paths.set_value(doc, "distribution[1].name", "c")
    assert doc["distribution"][1]["name"] == "c"


def test_set_top_level_key_creates_it():
    doc = {}
    paths.set_value(doc, "license", "https://creativecommons.org/licenses/by/4.0/")
    assert doc == {"license": "https://creativecommons.org/licenses/by/4.0/"}


def test_leaves_yields_scalar_paths():
    doc = {"name": "x", "keywords": ["a", "b"], "creator": {"name": "y"}}
    assert dict(paths.leaves(doc)) == {
        "name": "x", "keywords[0]": "a", "keywords[1]": "b", "creator.name": "y"}
