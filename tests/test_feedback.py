from pathlib import Path

import yaml

from boulangerie import feedback

FORM = Path(__file__).parents[1] / ".github" / "ISSUE_TEMPLATE" / "croissant-feedback.yml"

# What GitHub renders for a submitted issue form.
BODY = """### Which resource is this about?

tiny-kb

### How are you connected to this resource?

I maintain or run it

### Name

_No response_

### Description

_No response_

### License

CC BY 4.0

### How to cite

Smith J, et al. Tiny KB: a tiny knowledge base. NAR 2026.

### Access conditions

_No response_

### Current version or release

_No response_

### Anything else?

You missed our FTP site: ftp://example.org/pub
"""


FORMS_DIR = Path(__file__).parents[1] / ".github" / "ISSUE_TEMPLATE"


def test_committed_forms_match_their_definitions():
    # Run `uv run boulangerie write-forms` after changing feedback.FORMS.
    for form in feedback.FORMS.values():
        assert (FORMS_DIR / form.filename).read_text() == form.to_yaml(), form.filename


def test_form_labels_match_parser():
    form = yaml.safe_load(FORM.read_text())
    form_fields = {item["attributes"]["label"]: item["id"] for item in form["body"] if "id" in item}
    assert form_fields == feedback.FORMS["croissant-feedback"].labels()


def test_parse_issue_form_keeps_only_answered_fields():
    form, answers = feedback.parse_issue_form(BODY)
    assert form == "croissant-feedback"
    assert answers == {
        "kb": "tiny-kb",
        "relationship": "I maintain or run it",
        "license": "CC BY 4.0",
        "citation": "Smith J, et al. Tiny KB: a tiny knowledge base. NAR 2026.",
        "other": "You missed our FTP site: ftp://example.org/pub",
    }


def test_parse_handles_windows_line_endings():
    assert feedback.parse_issue_form(BODY.replace("\n", "\r\n"))[1]["license"] == "CC BY 4.0"


def test_license_names_become_urls():
    assert feedback.normalize_license("CC BY 4.0") == "https://creativecommons.org/licenses/by/4.0/"
    assert feedback.normalize_license("cc0.") == "https://creativecommons.org/publicdomain/zero/1.0/"
    assert feedback.normalize_license("https://example.org/license") == "https://example.org/license"


def test_apply_feedback_updates_croissant_and_provenance(tiny_croissant):
    provenance = {}
    form, answers = feedback.parse_issue_form(BODY)
    requested = feedback.requested_changes(form, answers)
    changes = feedback.apply_feedback(tiny_croissant, provenance, requested, "https://github.com/o/r/issues/7")
    assert tiny_croissant["license"] == "https://creativecommons.org/licenses/by/4.0/"
    assert tiny_croissant["citeAs"].startswith("Smith J")
    assert [c["field"] for c in changes] == ["license", "citeAs"]
    assert changes[0]["old"] == "Not Found"
    assert provenance["license"]["source"] == "maintainer"
    assert provenance["license"]["url"] == "https://github.com/o/r/issues/7"


def test_unchanged_answers_produce_no_changes(tiny_croissant):
    changes = feedback.apply_feedback(tiny_croissant, {}, {"name": "Tiny KB"}, "u")
    assert changes == []


FILE_PROBLEM = """### Which resource is this about?

cadre

### Which file?

2026 › 01 › CADRE_Ancillary_Data_Elements_2025_10_08.csv

### What's wrong?

It shouldn't be listed (old, internal, test, ...)

### Tell us more (optional)

_No response_
"""


def test_each_form_is_recognized_from_its_questions():
    form, answers = feedback.parse_issue_form(FILE_PROBLEM)
    assert form == "file-problem"
    assert answers == {"kb": "cadre", "item": "2026 › 01 › CADRE_Ancillary_Data_Elements_2025_10_08.csv",
                       "problem": "It shouldn't be listed (old, internal, test, ...)"}
    assert feedback.requested_changes(form, answers) == {}  # a person handles these
    assert not feedback.FORMS[form].applies_changes


def test_fix_detail_changes_one_field_and_records_its_source(tiny_croissant):
    body = ("### Which resource is this about?\n\ntiny-kb\n\n### Which detail?\n\nLicense\n\n"
            "### What should it say?\n\nCC0\n\n### Where is this stated? (optional)\n\nhttps://example.org/terms\n")
    form, answers = feedback.parse_issue_form(body)
    assert form == "fix-detail"
    requested = feedback.requested_changes(form, answers)
    assert requested == {"license": "CC0"}
    provenance = {}
    feedback.apply_feedback(tiny_croissant, provenance, requested, "https://github.com/o/r/issues/9",
                            source_url=answers["source"])
    assert tiny_croissant["license"] == "https://creativecommons.org/publicdomain/zero/1.0/"
    assert provenance["license"]["url"] == "https://example.org/terms"
    assert provenance["license"]["issue"] == "https://github.com/o/r/issues/9"


def test_fix_detail_for_an_unknown_detail_changes_nothing():
    assert feedback.requested_changes("fix-detail", {"detail": "Shoe size", "value": "9"}) == {}


def test_form_url_prefills_resource_title_and_fields():
    url = feedback.form_url("o/r", "fix-detail", "cadre", "CADRE", detail="License")
    assert url.startswith("https://github.com/o/r/issues/new?template=fix-detail.yml&kb=cadre")
    assert "title=Fix+a+detail+for+CADRE" in url and "detail=License" in url


def test_pr_body_mentions_issue_and_notes():
    body = feedback.render_pr_body("Tiny KB", 7, [{"field": "license", "old": None, "new": "x"}],
                                   {"other": "hello"}, "Looks good")
    assert "Closes #7" in body
    assert "_(empty)_" in body
    assert "hello" in body


def test_crawler_access_form_goes_to_a_person_and_warns_about_secrets():
    form = feedback.FORMS["crawler-access"]
    assert not form.applies_changes
    assert "Never paste a password, token or secret" in form.intro
    body = ("### Which resource is this about?\n\ncivic\n\n### How did you let us in?\n\n"
            "I allowed RoboCroissantBot in robots.txt\n\n### Link to your file list, if you published one\n\n_No response_\n")
    key, answers = feedback.parse_issue_form(body)
    assert key == "crawler-access"
    assert answers == {"kb": "civic", "method": "I allowed RoboCroissantBot in robots.txt"}
