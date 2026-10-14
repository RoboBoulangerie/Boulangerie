"""Turn a maintainer's answers from the GitHub issue form into croissant edits.

The issue form (``.github/ISSUE_TEMPLATE/croissant-feedback.yml``) renders each
answer under a ``### <label>`` heading. ``FEEDBACK_FIELDS`` is the single list
of those labels; a test keeps it in sync with the form.
"""

import re

from boulangerie import paths

NO_RESPONSE = "_No response_"

# (form field id, form label, croissant field or None if a human must handle it)
FEEDBACK_FIELDS = [
    ("kb", "Which resource is this about?", None),
    ("item", "Which file or table? (if it's about one)", None),
    ("relationship", "How are you connected to this resource?", None),
    ("name", "Name", "name"),
    ("description", "Description", "description"),
    ("license", "License", "license"),
    ("citation", "How to cite", "citeAs"),
    ("access", "Access conditions", "conditionsOfAccess"),
    ("version", "Current version or release", "version"),
    ("other", "Anything else?", None),
]
LABEL_TO_ID = {label: field_id for field_id, label, _ in FEEDBACK_FIELDS}
ID_TO_CROISSANT = {field_id: target for field_id, _, target in FEEDBACK_FIELDS if target}

# Common ways people write a license, mapped to the canonical URL.
LICENSE_URLS = {
    "cc0": "https://creativecommons.org/publicdomain/zero/1.0/",
    "cc0 1.0": "https://creativecommons.org/publicdomain/zero/1.0/",
    "cc by 4.0": "https://creativecommons.org/licenses/by/4.0/",
    "cc-by 4.0": "https://creativecommons.org/licenses/by/4.0/",
    "cc-by-4.0": "https://creativecommons.org/licenses/by/4.0/",
    "cc by 3.0": "https://creativecommons.org/licenses/by/3.0/",
    "cc-by-3.0": "https://creativecommons.org/licenses/by/3.0/",
    "cc by-sa 4.0": "https://creativecommons.org/licenses/by-sa/4.0/",
    "cc-by-sa-4.0": "https://creativecommons.org/licenses/by-sa/4.0/",
    "cc by-nc 4.0": "https://creativecommons.org/licenses/by-nc/4.0/",
    "cc-by-nc-4.0": "https://creativecommons.org/licenses/by-nc/4.0/",
    "cc by-nc-sa 4.0": "https://creativecommons.org/licenses/by-nc-sa/4.0/",
    "cc-by-nc-sa-4.0": "https://creativecommons.org/licenses/by-nc-sa/4.0/",
    "mit": "https://opensource.org/licenses/MIT",
    "apache 2.0": "https://www.apache.org/licenses/LICENSE-2.0",
    "apache-2.0": "https://www.apache.org/licenses/LICENSE-2.0",
    "odc-by": "https://opendatacommons.org/licenses/by/1-0/",
    "odbl": "https://opendatacommons.org/licenses/odbl/1-0/",
    "pddl": "https://opendatacommons.org/licenses/pddl/1-0/",
}


def parse_issue_form(body: str) -> dict[str, str]:
    """Return {field id: answer} for answered fields. Unknown headings are ignored."""
    answers: dict[str, str] = {}
    sections = re.split(r"^### +(.+?)\s*$", body.replace("\r\n", "\n"), flags=re.MULTILINE)
    # re.split with one group gives [preamble, heading1, text1, heading2, text2, ...]
    for heading, text in zip(sections[1::2], sections[2::2]):
        field_id = LABEL_TO_ID.get(heading.strip())
        value = text.strip()
        if field_id and value and value != NO_RESPONSE:
            answers[field_id] = value
    return answers


def normalize_license(answer: str) -> str:
    cleaned = answer.strip().strip(".")
    return LICENSE_URLS.get(cleaned.lower(), cleaned)


def apply_feedback(croissant: dict, provenance: dict[str, dict], answers: dict[str, str], issue_url: str) -> list[dict]:
    """Apply answers in place. Returns the list of changes: {field, old, new}."""
    changes = []
    for field_id, target in ID_TO_CROISSANT.items():
        if field_id not in answers:
            continue
        new = answers[field_id]
        if field_id == "license":
            new = normalize_license(new)
        old = croissant.get(target)
        if old == new:
            continue
        paths.set_value(croissant, target, new)
        provenance[target] = {
            "path": target, "value": new, "url": issue_url, "confidence": 1.0, "source": "maintainer",
        }
        changes.append({"field": target, "old": old, "new": new})
    return changes


def render_pr_body(kb_name: str, issue_number: int, changes: list[dict], answers: dict[str, str], check_summary: str) -> str:
    lines = [
        f"Updates the {kb_name} croissant with the answers from #{issue_number}.",
        "",
        f"Closes #{issue_number}",
        "",
        "## Changes",
        "",
    ]
    for c in changes:
        old = c["old"] if c["old"] not in (None, "") else "_(empty)_"
        lines += [f"**{c['field']}**", "", f"- Before: {old}", f"- After: {c['new']}", ""]
    if not changes:
        lines += ["No fields changed; see the notes below.", ""]
    if "item" in answers:
        lines += [f"About this file or table: {answers['item']}", ""]
    if "other" in answers:
        lines += ["## Other notes from the maintainer (need a human)", "", answers["other"], ""]
    if "relationship" in answers:
        lines += [f"Submitter's connection to the resource: {answers['relationship']}", ""]
    lines += ["## Checks after this change", "", check_summary]
    return "\n".join(lines)
