import copy
import json

import pytest

from boulangerie import checks
from boulangerie.filesets import collapse_filesets

FIELDS = [("chrom", "sc:Text"), ("pos", "sc:Integer")]


def _croissant(n_members=4, archive_prefix="", extra_member=None, schemas=None):
    """An archive with n scaffold_N.tsv members, each with its own record set."""
    dists = [{
        "@id": "archive", "@type": "cr:FileObject", "name": "scaffolds.zip",
        "contentUrl": "https://example.org/scaffolds.zip", "encodingFormat": "application/zip",
        "sha256": "0" * 64,
    }]
    record_sets = []
    for i in range(n_members):
        fid = f"member-{i}"
        dists.append({
            "@id": fid, "@type": "cr:FileObject", "name": f"scaffold_{i}.tsv",
            "contentUrl": f"{archive_prefix}scaffold_{i}.tsv", "encodingFormat": "text/tab-separated-values",
            "containedIn": {"@id": "archive"}, "contentSize": "1000 B",
        })
        fields = (schemas[i] if schemas else FIELDS)
        record_sets.append({
            "@id": f"rs-{i}", "@type": "cr:RecordSet", "name": f"Records from scaffold_{i}.tsv",
            "field": [{
                "@id": f"rs-{i}/{name}", "@type": "cr:Field", "name": name, "dataType": dtype,
                "description": f"Column {name} of scaffold_{i}.tsv",
                "source": {"fileObject": {"@id": fid}, "extract": {"column": name}},
            } for name, dtype in fields],
        })
    if extra_member:
        dists.append(extra_member)
    return {"@type": "sc:Dataset", "name": "T", "distribution": dists, "recordSet": record_sets}


def test_collapses_members_into_one_fileset_and_one_record_set():
    croissant = _croissant(4)
    original = copy.deepcopy(croissant)
    result, groups = collapse_filesets(croissant, min_group=3)

    assert croissant == original  # input untouched
    assert [g.files for g in groups] == [4]
    archive, fileset = result["distribution"]
    assert archive["@id"] == "archive"
    assert fileset["@type"] == "cr:FileSet"
    assert fileset["includes"] == "scaffold_*.tsv"
    assert fileset["containedIn"] == {"@id": "archive"}
    assert fileset["encodingFormat"] == "text/tab-separated-values"
    assert "4 files" in fileset["description"] and "4.0 KB" in fileset["description"]

    [rs] = result["recordSet"]
    assert rs["name"] == "Records from scaffold_*.tsv"
    assert [f["source"] for f in rs["field"]] == [
        {"fileSet": {"@id": fileset["@id"]}, "extract": {"column": name}} for name, _ in FIELDS]


def test_small_groups_are_left_alone():
    croissant = _croissant(4)
    result, groups = collapse_filesets(croissant, min_group=5)
    assert groups == [] and result == croissant


def test_members_with_different_columns_are_not_merged():
    # Same name pattern, two column layouts: one glob can't tell them apart, so nothing is collapsed.
    other = [("chrom", "sc:Text"), ("score", "sc:Float")]
    croissant = _croissant(6, schemas=[FIELDS] * 3 + [other] * 3)
    result, groups = collapse_filesets(croissant, min_group=3)
    assert groups == [] and result == croissant


def test_glob_that_would_catch_other_files_is_not_used():
    # scaffold_9.tsv.bak doesn't match the glob, but scaffold_99.tsv in another format would.
    stray = {"@id": "stray", "@type": "cr:FileObject", "contentUrl": "scaffold_99.tsv",
             "encodingFormat": "text/plain", "containedIn": {"@id": "archive"}}
    result, groups = collapse_filesets(_croissant(4, extra_member=stray), min_group=3)
    assert groups == []


def test_archive_bang_prefix_is_dropped_from_the_glob():
    result, _ = collapse_filesets(_croissant(4, archive_prefix="scaffolds.zip!/"), min_group=3)
    assert result["distribution"][1]["includes"] == "scaffold_*.tsv"


def test_files_not_in_an_archive_are_never_grouped():
    croissant = _croissant(4)
    for d in croissant["distribution"][1:]:
        del d["containedIn"]
        d["contentUrl"] = "https://example.org/" + d["contentUrl"]
    _, groups = collapse_filesets(croissant, min_group=3)
    assert groups == []


def test_record_set_reading_several_files_blocks_grouping():
    croissant = _croissant(4)
    croissant["recordSet"][0]["field"][1]["source"]["fileObject"]["@id"] = "member-1"
    _, groups = collapse_filesets(croissant, min_group=3)
    assert all("member-0" not in json.dumps(g.__dict__) for g in groups)
    result, _ = collapse_filesets(croissant, min_group=3)
    ids = {d["@id"] for d in result["distribution"]}
    assert {"member-0", "member-1"} <= ids  # left as they were


def test_result_has_no_new_problems_and_passes_the_official_validator(tmp_path):
    croissant = _croissant(25)
    croissant["@context"] = json.loads(
        (__import__("pathlib").Path(__file__).parent / "fixtures/kbs/tiny-kb/croissant.json").read_text())["@context"]
    croissant.update({"conformsTo": checks.CROISSANT_1_1, "description": "d", "url": "https://example.org/",
                      "license": "https://creativecommons.org/publicdomain/zero/1.0/"})
    result, groups = collapse_filesets(croissant)
    assert [g.files for g in groups] == [25]
    assert not [f for f in checks.run_offline_checks(result) if f.severity == checks.PROBLEM]

    path = tmp_path / "croissant.json"
    path.write_text(json.dumps(result))
    errors = [f.technical for f in checks.run_mlcroissant(path) if f.check == "mlcroissant_error"]
    assert errors == []


def test_unknown_reference_to_a_removed_record_set_fails_loudly():
    croissant = _croissant(4)
    croissant["recordSet"].append({"@id": "uses-rs-2", "@type": "cr:RecordSet", "field": [{
        "@id": "uses-rs-2/x", "@type": "cr:Field", "name": "x", "references": {"field": {"@id": "rs-2/chrom"}},
        "source": {"fileObject": {"@id": "archive"}},
    }]})
    with pytest.raises(ValueError, match="dangling"):
        collapse_filesets(croissant, min_group=3)
