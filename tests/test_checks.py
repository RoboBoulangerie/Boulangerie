import copy

from boulangerie import checks
from boulangerie.checks import NOTE, PROBLEM, WARNING


def _by_check(findings):
    out = {}
    for f in findings:
        out.setdefault(f.check, []).append(f)
    return out


def test_not_found_license_is_a_problem(tiny_croissant):
    found = _by_check(checks.check_core_fields(tiny_croissant))
    [license_finding] = [f for f in found["missing_core_field"] if f.path == "license"]
    assert license_finding.severity == PROBLEM
    assert "'Not Found'" in license_finding.message


def test_missing_citation_is_a_warning_and_version_a_note(tiny_croissant):
    found = {f.path: f.severity for f in checks.check_core_fields(tiny_croissant)}
    assert found["citeAs"] == WARNING
    assert found["version"] == NOTE
    assert "name" not in found and "description" not in found


def test_wrong_conforms_to_is_fixable(tiny_croissant):
    tiny_croissant["conformsTo"] = "http://mlcommons.org/croissant/1.0"
    [f] = [f for f in checks.check_core_fields(tiny_croissant) if f.check == "conforms_to"]
    assert f.fix == {"path": "conformsTo", "value": checks.CROISSANT_1_1}


def test_license_terms_page():
    croissant = {"license": "https://www.ebi.ac.uk/about/terms-of-use/"}
    assert [f.check for f in checks.check_license_is_terms_page(croissant)] == ["license_is_terms_page"]
    croissant = {"license": "https://creativecommons.org/licenses/by/4.0/"}
    assert checks.check_license_is_terms_page(croissant) == []


def test_format_extension_mismatch_has_fix(tiny_croissant):
    found = _by_check(checks.check_distributions(tiny_croissant))
    [f] = found["format_extension_mismatch"]
    assert f.path == "distribution[0].encodingFormat"
    assert f.fix == {"path": "distribution[0].encodingFormat", "value": "text/tab-separated-values"}


def test_duplicate_ids_and_repeated_urls_reported_once():
    dist = {"@id": "a", "@type": "cr:FileObject", "contentUrl": "https://x/data.zip",
            "encodingFormat": "application/zip"}
    croissant = {"distribution": [dist, copy.deepcopy(dist), copy.deepcopy(dist)]}
    found = _by_check(checks.check_distributions(croissant))
    assert len(found["duplicate_distribution_id"]) == 1
    assert len(found["repeated_content_url"]) == 1


def test_relative_url_and_bad_checksum():
    croissant = {"distribution": [{"@id": "a", "@type": "cr:FileObject", "contentUrl": "output/a.csv",
                                   "encodingFormat": "text/csv", "md5": "xyz"}]}
    found = _by_check(checks.check_distributions(croissant))
    assert set(found) == {"relative_content_url", "invalid_checksum"}


def test_files_inside_archives_may_use_relative_paths():
    croissant = {"distribution": [
        {"@id": "zip", "@type": "cr:FileObject", "contentUrl": "https://x/a.zip", "encodingFormat": "application/zip",
         "sha256": "0" * 64},
        {"@id": "inner", "@type": "cr:FileObject", "contentUrl": "a.csv", "encodingFormat": "text/csv",
         "containedIn": {"@id": "zip"}},
    ]}
    assert checks.check_distributions(croissant) == []
    assert checks.check_compressed_without_inner_file(croissant) == []


def test_archive_without_inner_files_is_a_note():
    croissant = {"distribution": [{"@id": "zip", "@type": "cr:FileObject", "contentUrl": "https://x/a.tar.gz"}]}
    [f] = checks.check_compressed_without_inner_file(croissant)
    assert f.severity == NOTE


def test_no_files_is_a_problem():
    [f] = checks.check_distributions({"distribution": []})
    assert (f.check, f.severity) == ("no_files", PROBLEM)


def test_broken_source_reference(tiny_croissant):
    found = _by_check(checks.check_record_sets(tiny_croissant))
    [f] = found["broken_source_reference"]
    assert f.path == "recordSet[0].field[1].source"


def test_parent_fields_with_subfields_need_no_source():
    croissant = {"distribution": [{"@id": "f"}], "recordSet": [{"field": [
        {"name": "location", "subField": [
            {"name": "latitude", "source": {"fileObject": {"@id": "f"}, "extract": {"column": "lat"}}},
            {"name": "longitude"},
        ]},
    ]}]}
    [f] = checks.check_record_sets(croissant)
    assert f.check == "field_missing_source"
    assert f.path == "recordSet[0].field[0].subField[1]"


def test_placeholder_column_names_and_inline_data():
    croissant = {"distribution": [{"@id": "f"}], "recordSet": [{
        "field": [{"name": "column_1", "source": {"fileObject": {"@id": "f"}}}],
        "data": [{"column_1": "x"}],
    }]}
    assert {f.check for f in checks.check_record_sets(croissant)} == {"placeholder_column_name", "inline_data_rows"}


def test_run_offline_checks_sorts_most_severe_first(tiny_croissant):
    severities = [f.severity for f in checks.run_offline_checks(tiny_croissant)]
    order = [checks.SEVERITY_ORDER[s] for s in severities]
    assert order == sorted(order)


# Real output from `mlcroissant validate` (tests/fixtures/kbs/tiny-kb).
MLC_OUTPUT = """\
W1013 21:01:14.422017 8217362304 rdf.py:89] WARNING: The JSON-LD `@context` is not standard.
E1013 21:01:14.441725 8217362304 validate.py:55] Found the following 2 error(s) during the validation:
  -  [Metadata(Tiny KB) > FileObject(README.pdf)] At least one of these properties should be defined: ['md5', 'sha256'].
  -  [Metadata(Tiny KB) > RecordSet(genes)] There is a reference to node with UUID "file-missing" in node "genes", but this node doesn't exist.
Found the following 3 warning(s) during the validation:
  -  [Metadata(Tiny KB)] Property "http://mlcommons.org/croissant/citeAs" is recommended, but does not exist.
  -  [Metadata(Tiny KB)] Property "https://schema.org/version" is recommended, but does not exist.
  -  [Metadata(Tiny KB)] Something else entirely.
I1013 21:01:14.441800 8217362304 validate.py:53] Done.
"""


def test_parse_mlcroissant_output():
    findings = checks.parse_mlcroissant_output(MLC_OUTPUT)
    errors = [f for f in findings if f.check == "mlcroissant_error"]
    warnings = [f for f in findings if f.check == "mlcroissant_warning"]
    assert len(errors) == 2 and all(f.severity == PROBLEM for f in errors)
    assert "md5" in errors[0].technical
    # "is recommended" warnings duplicate our own checks and are dropped.
    assert [f.technical for f in warnings] == ["[Metadata(Tiny KB)] Something else entirely."]


def test_run_mlcroissant_on_real_file(tiny_kb):
    findings = checks.run_mlcroissant(tiny_kb.croissant_path)
    errors = [f.technical for f in findings if f.check == "mlcroissant_error"]
    assert any("file-missing" in e for e in errors)
