"""Run every check for a KB, store the results, and render them for people.

Results go to ``results/<slug>.json`` (not committed; CI regenerates and caches
them). The official validator is slow on very large croissants, so its output
is reused when the croissant file hasn't changed.
"""

import datetime
import hashlib
import json
from pathlib import Path
from urllib.parse import urlencode

from boulangerie import checks, online
from boulangerie.catalog import REPO_ROOT, KnowledgeBase, read_croissant, read_provenance
from boulangerie.checks import NOTE, PROBLEM, SEVERITY_ORDER, WARNING, Finding

RESULTS_DIR = REPO_ROOT / "results"
MAX_EXAMPLES = 5

STATUS_GOOD = "good"
STATUS_CHECK = "check"
STATUS_HELP = "help"
STATUS_LABELS = {
    STATUS_GOOD: "Looks good",
    STATUS_CHECK: "A few things to check",
    STATUS_HELP: "Needs your help",
}


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def results_path(kb: KnowledgeBase, results_dir: Path = RESULTS_DIR) -> Path:
    return results_dir / f"{kb.slug}.json"


def load_results(kb: KnowledgeBase, results_dir: Path = RESULTS_DIR) -> dict | None:
    path = results_path(kb, results_dir)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def group_findings(findings: list[Finding]) -> list[dict]:
    """Collapse repeated findings (e.g. 14,000 broken references) into one entry with examples."""
    groups: dict[tuple, dict] = {}
    for f in findings:
        key = (f.severity, f.check, f.title)
        g = groups.setdefault(key, {
            "severity": f.severity, "check": f.check, "title": f.title, "message": f.message,
            "count": 0, "examples": [], "fixable": 0,
        })
        g["count"] += 1
        if f.fix:
            g["fixable"] += 1
        if len(g["examples"]) < MAX_EXAMPLES:
            g["examples"].append({"path": f.path, "technical": f.technical})
    return sorted(groups.values(), key=lambda g: (SEVERITY_ORDER[g["severity"]], -g["count"], g["check"]))


def status_for(findings: list[Finding]) -> str:
    severities = {f.severity for f in findings}
    if PROBLEM in severities:
        return STATUS_HELP
    if WARNING in severities:
        return STATUS_CHECK
    return STATUS_GOOD


MLC_ALWAYS = "always"  # run the official validator
MLC_CHANGED = "changed"  # run it only if the croissant changed since the last results
MLC_CACHED = "cached"  # never run it; reuse earlier results if the croissant is unchanged
MLC_MODES = (MLC_ALWAYS, MLC_CHANGED, MLC_CACHED)


def run_all_checks(
    kb: KnowledgeBase,
    *,
    with_online: bool,
    mlcroissant: str = MLC_CACHED,
    results_dir: Path = RESULTS_DIR,
) -> dict:
    croissant = read_croissant(kb)
    provenance = read_provenance(kb)
    sha = file_sha256(kb.croissant_path)
    previous = load_results(kb, results_dir)
    unchanged = previous is not None and previous.get("croissant_sha256") == sha

    findings = checks.run_offline_checks(croissant)

    mlc: list[Finding] | None = None
    if unchanged and previous.get("mlcroissant") is not None and mlcroissant != MLC_ALWAYS:
        mlc = [Finding(**f) for f in previous["mlcroissant"]]
    elif mlcroissant in (MLC_ALWAYS, MLC_CHANGED):
        mlc = checks.run_mlcroissant(kb.croissant_path)
    if mlc:
        findings += mlc

    grounding_levels: dict[str, str] = {}
    links: dict = {}
    if with_online:
        grounding_findings, grounding_levels = online.check_grounding(croissant, provenance)
        link_findings, links = online.check_links(croissant)
        findings += grounding_findings + link_findings
    elif unchanged:
        # Keep the last online results until they're refreshed.
        grounding_levels = previous.get("grounding", {})
        links = previous.get("links", {})
        findings += [Finding(**f) for f in previous.get("online_findings", [])]

    findings = checks.sort_findings(findings)
    online_checks = {"grounding_mismatch", "grounding_unverified", "grounding_no_source", "broken_link"}
    results = {
        "kb": kb.name,
        "slug": kb.slug,
        "checked_at": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
        "croissant_sha256": sha,
        "status": status_for(findings),
        "counts": {s: sum(f.severity == s for f in findings) for s in (PROBLEM, WARNING, NOTE)},
        "groups": group_findings(findings),
        "fixes": [f.fix for f in findings if f.fix],
        "grounding": grounding_levels,
        "links": links,
        "mlcroissant": [f.to_dict() for f in mlc] if mlc is not None else None,
        "online_findings": [f.to_dict() for f in findings if f.check in online_checks],
    }
    results_dir.mkdir(parents=True, exist_ok=True)
    results_path(kb, results_dir).write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return results


# ── Markdown for GitHub issues and PR comments ────────────────────────────────

SEVERITY_ICONS = {PROBLEM: "🔴", WARNING: "🟡", NOTE: "🔵"}
SEVERITY_WORDS = {PROBLEM: "Needs fixing", WARNING: "Worth checking", NOTE: "Nice to have"}
ISSUE_MARKER = "<!-- boulangerie-check -->"


def feedback_url(kb: KnowledgeBase, repo: str) -> str:
    """Link to the correction form with the resource and title already filled in."""
    query = urlencode({"template": "croissant-feedback.yml", "kb": kb.slug, "title": f"Correction for {kb.name}"})
    return f"https://github.com/{repo}/issues/new?{query}"


def render_markdown(kb: KnowledgeBase, results: dict, site_url: str, repo: str) -> str:
    status = results["status"]
    page = f"{site_url.rstrip('/')}/kb/{kb.slug}/"
    feedback = feedback_url(kb, repo)
    lines = [
        ISSUE_MARKER,
        f"## {kb.name}: {STATUS_LABELS[status]}",
        "",
        f"This is an automatic check of the [Croissant metadata for {kb.name}]({page}). "
        "It is updated every time the croissant changes and once a week.",
        "",
    ]
    if kb.maintainers:
        lines += ["Maintainers: " + " ".join(f"@{m}" for m in kb.maintainers), ""]
    c = results["counts"]
    lines += [
        f"| {SEVERITY_ICONS[PROBLEM]} Needs fixing | {SEVERITY_ICONS[WARNING]} Worth checking | {SEVERITY_ICONS[NOTE]} Nice to have |",
        "|---|---|---|",
        f"| {c[PROBLEM]} | {c[WARNING]} | {c[NOTE]} |",
        "",
    ]
    if not results["groups"]:
        lines += ["Everything we check looks good. 🎉", ""]
    for g in results["groups"]:
        count = f" ({g['count']:,} places)" if g["count"] > 1 else ""
        lines += [f"### {SEVERITY_ICONS[g['severity']]} {g['title']}{count}", "", g["message"], ""]
        if g["fixable"]:
            lines += [f"_We can fix {'this' if g['fixable'] == 1 else 'these'} automatically._", ""]
        lines += ["<details><summary>Technical details</summary>", ""]
        for ex in g["examples"]:
            where = f"`{ex['path']}`: " if ex["path"] else ""
            lines.append(f"- {where}{ex['technical']}")
        if g["count"] > len(g["examples"]):
            lines.append(f"- …and {g['count'] - len(g['examples']):,} more")
        lines += ["", "</details>", ""]
    lines += [
        "---",
        f"Something here wrong, or know the answer? [Tell us here]({feedback}). "
        "No Croissant knowledge needed.",
        "",
        f"<sub>Checked {results['checked_at']} · croissant `{results['croissant_sha256'][:12]}`</sub>",
    ]
    return "\n".join(lines)
