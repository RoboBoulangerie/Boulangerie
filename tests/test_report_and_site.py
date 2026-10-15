import json

from boulangerie import report, site, summary
from boulangerie.catalog import load_catalog
from boulangerie.checks import NOTE, PROBLEM, WARNING, Finding


def _finding(severity, check="c", path="p", fix=None):
    return Finding(check=check, severity=severity, path=path, title=f"title {check}", message="m",
                   technical="t", fix=fix)


def test_status_for():
    assert report.status_for([]) == report.STATUS_GOOD
    assert report.status_for([_finding(NOTE)]) == report.STATUS_GOOD
    assert report.status_for([_finding(WARNING), _finding(NOTE)]) == report.STATUS_CHECK
    assert report.status_for([_finding(PROBLEM)]) == report.STATUS_HELP


def test_group_findings_collapses_and_caps_examples():
    findings = [_finding(WARNING, "many", f"x[{i}]", fix={"path": "x", "value": 1}) for i in range(12)]
    findings.append(_finding(PROBLEM, "one"))
    groups = report.group_findings(findings)
    assert [g["check"] for g in groups] == ["one", "many"]
    assert groups[1]["count"] == 12
    assert groups[1]["fixable"] == 12
    assert len(groups[1]["examples"]) == report.MAX_EXAMPLES


def test_run_all_checks_writes_results(tiny_kb, tmp_path):
    results = report.run_all_checks(tiny_kb, with_online=False, results_dir=tmp_path)
    assert results["status"] == report.STATUS_HELP
    assert results["mlcroissant"] is None  # cached mode never runs the validator
    on_disk = json.loads((tmp_path / "tiny-kb.json").read_text())
    assert on_disk["croissant_sha256"] == results["croissant_sha256"]


def test_validator_results_are_reused_while_croissant_is_unchanged(tiny_kb, tmp_path):
    first = report.run_all_checks(tiny_kb, with_online=False, mlcroissant=report.MLC_CHANGED, results_dir=tmp_path)
    assert first["mlcroissant"]
    again = report.run_all_checks(tiny_kb, with_online=False, mlcroissant=report.MLC_CACHED, results_dir=tmp_path)
    assert again["mlcroissant"] == first["mlcroissant"]

    tiny_kb.croissant_path.write_text(tiny_kb.croissant_path.read_text().replace("Tiny KB collects", "Tiny KB has"))
    changed = report.run_all_checks(tiny_kb, with_online=False, mlcroissant=report.MLC_CACHED, results_dir=tmp_path)
    assert changed["mlcroissant"] is None


def test_render_markdown(tiny_kb, tmp_path):
    results = report.run_all_checks(tiny_kb, with_online=False, results_dir=tmp_path)
    md = report.render_markdown(tiny_kb, results, "https://site.example/", "o/r")
    assert md.startswith(report.ISSUE_MARKER)
    assert "Tiny KB: Needs your help" in md
    assert "@octocat" in md
    assert "https://site.example/kb/tiny-kb/" in md
    assert "kb=tiny-kb" in md and "title=Correction+for+Tiny+KB" in md


def test_build_site(kbs_dir, tmp_path):
    catalog = load_catalog(kbs_dir)
    results_dir = tmp_path / "results"
    report.run_all_checks(catalog[0], with_online=False, results_dir=results_dir)
    out = tmp_path / "_site"
    site.build_site(catalog, out, results_dir=results_dir)

    index = (out / "index.html").read_text()
    page = (out / "kb" / "tiny-kb" / "index.html").read_text()
    assert "Tiny KB" in index and "Needs your help" in index
    published = (out / "kb" / "tiny-kb" / "croissant.json").read_text()
    assert json.loads(published)["name"] == "Tiny KB"
    assert published.startswith('{\n  "@context"')  # indented, so people can read it
    assert (out / "about" / "index.html").exists()
    assert (out / "static" / "style.css").exists()
    # Values from the croissant are escaped, and missing fields ask for help.
    assert "&lt;small&gt; facts about genes &amp; diseases" in page
    assert "We couldn't find this on your website." in page
    assert "Found on <a href=\"https://example.org/about\">" in page
    assert json.loads((out / "catalog.json").read_text())[0]["slug"] == "tiny-kb"
    # Every file and table is published for review, and the KB page links to it.
    review_data = json.loads((out / "kb" / "tiny-kb" / "review.json").read_text())
    assert review_data["counts"]["files"] == 2 and review_data["counts"]["tables"] == 1
    assert (out / "kb" / "tiny-kb" / "review" / "index.html").exists()
    assert 'href="review/index.html#files"' in page


def test_parse_and_show_sizes():
    assert summary.parse_size("1.5 MB") == 1_500_000
    assert summary.parse_size("2048 B") == 2048
    assert summary.parse_size("3 KiB") == 3072
    assert summary.parse_size(42) == 42
    assert summary.parse_size("big") is None
    assert summary.human_size(999) == "999 bytes"
    assert summary.human_size(1_500_000) == "1.5 MB"


def test_summarize(tiny_croissant):
    s = summary.summarize(tiny_croissant)
    assert s["file_count"] == 2
    assert s["total_size"] == "1.5 MB"
    assert {g["format"] for g in s["formats"]} == {"CSV spreadsheet", "PDF document"}
    assert s["table_count"] == 1 and s["column_count"] == 2


def test_link_dois():
    html = str(summary.link_dois("Seal RL, et al. Nucleic Acids Res. 2023;51(D1):D1003. doi: 10.1093/nar/gkac888."))
    assert '<a href="https://doi.org/10.1093/nar/gkac888">10.1093/nar/gkac888</a>.' in html
    html = str(summary.link_dois("See https://doi.org/10.1093/genetics/iyae049 (2024)"))
    assert '<a href="https://doi.org/10.1093/genetics/iyae049">https://doi.org/10.1093/genetics/iyae049</a> (2024)' in html


def test_link_dois_escapes_everything_else():
    html = str(summary.link_dois("<b>Tiny</b> & co, doi:10.1234/a<b>"))
    assert "&lt;b&gt;Tiny&lt;/b&gt; &amp; co" in html
    assert '<a href="https://doi.org/10.1234/a">10.1234/a</a>&lt;b&gt;' in html
    assert str(summary.link_dois("No identifiers here.")) == "No identifiers here."


def test_link_dois_written_as_bare_doi_org():
    html = str(summary.link_dois("Nat Genet. 49, 170–174 (2017); doi: doi.org/10.1038/ng.3774. *Equal"))
    assert 'doi: <a href="https://doi.org/10.1038/ng.3774">doi.org/10.1038/ng.3774</a>. *Equal' in html
