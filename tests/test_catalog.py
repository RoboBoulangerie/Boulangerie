import pytest

from boulangerie.catalog import (
    find_kb, load_catalog, read_croissant, read_provenance, slugify, write_croissant, write_provenance,
)


def test_load_catalog(kbs_dir):
    [kb] = load_catalog(kbs_dir)
    assert kb.slug == "tiny-kb"
    assert kb.name == "Tiny KB"
    assert kb.maintainers == ("octocat",)
    assert kb.croissant_path.name == "croissant.json"


def test_find_kb_by_name_or_slug(kbs_dir):
    catalog = load_catalog(kbs_dir)
    assert find_kb(catalog, "Tiny KB").slug == "tiny-kb"
    assert find_kb(catalog, "tiny-kb").slug == "tiny-kb"
    assert find_kb(catalog, " tiny kb ").slug == "tiny-kb"
    with pytest.raises(KeyError):
        find_kb(catalog, "Nope")


def test_slugify():
    assert slugify("Alliance of Genome Resources") == "alliance-of-genome-resources"
    assert slugify("From RRID to Resource Watch: A Knowledgebase") == "from-rrid-to-resource-watch-a-knowledgebase"


def test_large_croissants_are_gzipped_and_read_back(tiny_kb):
    croissant = read_croissant(tiny_kb)
    path = write_croissant(tiny_kb, croissant, gzip_threshold=100)
    assert path.name == "croissant.json.gz"
    assert not (tiny_kb.directory / "croissant.json").exists()
    assert tiny_kb.croissant_path == path
    assert read_croissant(tiny_kb) == croissant


def test_gzip_output_is_byte_stable(tiny_kb):
    croissant = read_croissant(tiny_kb)
    first = write_croissant(tiny_kb, croissant, gzip_threshold=100).read_bytes()
    second = write_croissant(tiny_kb, croissant, gzip_threshold=100).read_bytes()
    assert first == second


def test_small_croissant_goes_back_to_plain_json(tiny_kb):
    croissant = read_croissant(tiny_kb)
    write_croissant(tiny_kb, croissant, gzip_threshold=100)
    path = write_croissant(tiny_kb, croissant)
    assert path.name == "croissant.json"
    assert not (tiny_kb.directory / "croissant.json.gz").exists()


def test_provenance_round_trip(tiny_kb):
    provenance = read_provenance(tiny_kb)
    assert provenance["description"]["url"] == "https://example.org/about"
    provenance["license"] = {"path": "license", "value": "CC0", "url": "https://x", "confidence": 1.0}
    write_provenance(tiny_kb, provenance)
    assert set(read_provenance(tiny_kb)) == {"description", "name", "license"}
