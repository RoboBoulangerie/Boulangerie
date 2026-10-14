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


def test_form_labels_match_parser():
    form = yaml.safe_load(FORM.read_text())
    form_fields = [(item["id"], item["attributes"]["label"]) for item in form["body"] if "id" in item]
    assert form_fields == [(field_id, label) for field_id, label, _ in feedback.FEEDBACK_FIELDS]


def test_parse_issue_form_keeps_only_answered_fields():
    answers = feedback.parse_issue_form(BODY)
    assert answers == {
        "kb": "tiny-kb",
        "relationship": "I maintain or run it",
        "license": "CC BY 4.0",
        "citation": "Smith J, et al. Tiny KB: a tiny knowledge base. NAR 2026.",
        "other": "You missed our FTP site: ftp://example.org/pub",
    }


def test_parse_handles_windows_line_endings():
    assert feedback.parse_issue_form(BODY.replace("\n", "\r\n"))["license"] == "CC BY 4.0"


def test_license_names_become_urls():
    assert feedback.normalize_license("CC BY 4.0") == "https://creativecommons.org/licenses/by/4.0/"
    assert feedback.normalize_license("cc0.") == "https://creativecommons.org/publicdomain/zero/1.0/"
    assert feedback.normalize_license("https://example.org/license") == "https://example.org/license"


def test_apply_feedback_updates_croissant_and_provenance(tiny_croissant):
    provenance = {}
    answers = feedback.parse_issue_form(BODY)
    changes = feedback.apply_feedback(tiny_croissant, provenance, answers, "https://github.com/o/r/issues/7")
    assert tiny_croissant["license"] == "https://creativecommons.org/licenses/by/4.0/"
    assert tiny_croissant["citeAs"].startswith("Smith J")
    assert [c["field"] for c in changes] == ["license", "citeAs"]
    assert changes[0]["old"] == "Not Found"
    assert provenance["license"]["source"] == "maintainer"
    assert provenance["license"]["url"] == "https://github.com/o/r/issues/7"


def test_unchanged_answers_produce_no_changes(tiny_croissant):
    changes = feedback.apply_feedback(tiny_croissant, {}, {"name": "Tiny KB"}, "u")
    assert changes == []


def test_pr_body_mentions_issue_and_notes():
    body = feedback.render_pr_body("Tiny KB", 7, [{"field": "license", "old": None, "new": "x"}],
                                   {"other": "hello"}, "Looks good")
    assert "Closes #7" in body
    assert "_(empty)_" in body
    assert "hello" in body
