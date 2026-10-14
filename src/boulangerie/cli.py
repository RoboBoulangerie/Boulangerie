"""Command line entry point: ``uv run boulangerie <command>``."""

import argparse
import json
import os
import sys
import tomllib
from pathlib import Path

from boulangerie import checks, config, feedback, github, paths, provenance_import, report, site
from boulangerie.catalog import (
    KBS_DIR, KnowledgeBase, find_kb, load_catalog, load_kb, read_croissant, read_provenance,
    slugify, write_croissant, write_provenance,
)


def _selected(args) -> list[KnowledgeBase]:
    catalog = load_catalog()
    return [find_kb(catalog, args.kb)] if getattr(args, "kb", None) else catalog


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)  # JSON strings are valid TOML basic strings


def cmd_import_croissants(args) -> None:
    """Copy croissants from a folder of ``<KB name>.json`` files into ``kbs/``."""
    with open(args.kb_config, "rb") as f:
        configured = {kb["name"]: kb for kb in tomllib.load(f)["knowledge_bases"]}
    for path in sorted(Path(args.source).glob("*.json")):
        name = path.stem
        if name in args.skip:
            continue
        if name not in configured:
            raise KeyError(f"{name!r} is not in {args.kb_config}")
        meta = configured[name]
        croissant = json.loads(path.read_text(encoding="utf-8"))
        directory = KBS_DIR / slugify(name)
        directory.mkdir(parents=True, exist_ok=True)
        homepage = croissant.get("url") if isinstance(croissant.get("url"), str) else meta.get("source_url", meta["url"])
        (directory / "kb.toml").write_text(
            f"name = {_toml_string(name)}\n"
            f"title = {_toml_string(meta.get('title', name))}\n"
            f"homepage = {_toml_string(homepage)}\n"
            f"grant_number = {_toml_string(meta.get('grant_number', ''))}\n"
            "# GitHub usernames of people at the KB who should be notified about checks and corrections.\n"
            "maintainers = []\n",
            encoding="utf-8",
        )
        written = write_croissant(load_kb(directory), croissant)
        print(f"{name} -> {written.relative_to(KBS_DIR.parent)}")


def cmd_import_provenance(args) -> None:
    """Replace each KB's pipeline provenance from the database, keeping anything a maintainer confirmed."""
    db = Path(args.db).expanduser()
    for kb in _selected(args):
        imported = provenance_import.read_dataset_provenance(db, kb.name)
        existing = read_provenance(kb)
        confirmed = {p: e for p, e in existing.items() if e.get("source") == "maintainer"}
        write_provenance(kb, imported | confirmed)
        kept = f", kept {len(confirmed)} confirmed by maintainers" if confirmed else ""
        print(f"{kb.name}: {len(imported)} dataset-level fields{kept}")


def _selected_many(args) -> list[KnowledgeBase]:
    catalog = load_catalog()
    if getattr(args, "kbs", None):
        return [find_kb(catalog, k) for k in args.kbs]
    return _selected(args)


def cmd_check(args) -> int:
    validator_failed = []
    for kb in _selected_many(args):
        results = report.run_all_checks(kb, with_online=args.online, mlcroissant=args.mlcroissant)
        c = results["counts"]
        print(f"{kb.name}: {report.STATUS_LABELS[results['status']]} "
              f"({c['problem']} problems, {c['warning']} warnings, {c['note']} notes)", flush=True)
        if any(f["check"] == "mlcroissant_error" for f in results["mlcroissant"] or []):
            validator_failed.append(kb.name)
    if validator_failed and args.fail_on_validator_errors:
        print(f"The official validator reported errors for: {', '.join(validator_failed)}", file=sys.stderr)
        return 1
    return 0


def cmd_pr_summary(args) -> None:
    """Markdown summary of the checks for the KBs a pull request touches."""
    kbs = _selected_many(args)
    lines = ["### Croissant checks", ""]
    if not kbs:
        lines.append("This pull request doesn't change any croissants.")
    else:
        lines += ["| Resource | Status | Needs fixing | Worth checking | Nice to have | Official validator |",
                  "|---|---|---|---|---|---|"]
        for kb in kbs:
            r = report.load_results(kb)
            c = r["counts"]
            mlc = r["mlcroissant"]
            validator = "not run" if mlc is None else (
                "❌ errors" if any(f["check"] == "mlcroissant_error" for f in mlc) else "✅ passes")
            lines.append(f"| [{kb.name}]({config.SITE_URL.rstrip('/')}/kb/{kb.slug}/) | "
                         f"{report.STATUS_LABELS[r['status']]} | {c['problem']} | {c['warning']} | {c['note']} | {validator} |")
    Path(args.out).write_text("\n".join(lines) + "\n", encoding="utf-8")


def cmd_build_site(args) -> None:
    out = Path(args.out)
    site.build_site(_selected(args), out)
    print(f"Site written to {out}")


def cmd_sync_issues(args) -> None:
    for kb in _selected(args):
        results = report.load_results(kb)
        if results is None:
            raise FileNotFoundError(f"No results for {kb.name}; run `boulangerie check` first.")
        body = report.render_markdown(kb, results, config.SITE_URL, config.REPO)
        all_good = results["counts"]["problem"] == 0 and results["counts"]["warning"] == 0
        print(f"{kb.name}: {github.upsert_check_issue(config.REPO, kb.slug, kb.name, body, all_good)}")


def cmd_autofix(args) -> None:
    """Apply the fixes the checks know how to make, optionally as one PR per KB."""
    for kb in _selected(args):
        croissant = read_croissant(kb)
        fixes = [f.fix for f in checks.run_offline_checks(croissant) if f.fix]
        if not fixes:
            continue
        for fix in fixes:
            paths.set_value(croissant, fix["path"], fix["value"])
        write_croissant(kb, croissant)
        print(f"{kb.name}: applied {len(fixes)} automatic fixes")
        if args.open_prs:
            listing = "\n".join(f"- `{f['path']}` → `{f['value']}`" for f in fixes[:50])
            more = f"\n- …and {len(fixes) - 50} more" if len(fixes) > 50 else ""
            url = github.open_pull_request(
                config.REPO, branch=f"autofix/{kb.slug}", base=args.base,
                title=f"Automatic fixes for the {kb.name} croissant",
                body=f"These are mechanical fixes found by the croissant checks.\n\n{listing}{more}",
                files=[str(kb.directory)], message=f"Automatic fixes for {kb.name}",
            )
            print(f"  {url}")


def cmd_feedback(args) -> None:
    """Handle a feedback issue: apply the answers and open a PR for review."""
    event = json.loads(Path(args.event).read_text(encoding="utf-8"))
    issue = event["issue"]
    answers = feedback.parse_issue_form(issue["body"] or "")
    if "kb" not in answers:
        github.comment(config.REPO, issue["number"], "Thanks! We couldn't tell which resource this is about, "
                       "so a person will take a look.")
        return
    try:
        kb = find_kb(load_catalog(), answers["kb"])
    except KeyError:
        github.comment(config.REPO, issue["number"], f"Thanks! We don't have a resource called "
                       f"`{answers['kb']}`, so a person will take a look.")
        return

    croissant = read_croissant(kb)
    provenance = read_provenance(kb)
    changes = feedback.apply_feedback(croissant, provenance, answers, issue["html_url"])
    if not changes and "other" not in answers:
        github.comment(config.REPO, issue["number"], "Thanks! Everything you sent already matches the croissant, "
                       "so there's nothing to change.")
        return
    write_croissant(kb, croissant)
    write_provenance(kb, provenance)

    results = report.run_all_checks(kb, with_online=False, mlcroissant=report.MLC_ALWAYS)
    c = results["counts"]
    summary_line = (f"{report.STATUS_LABELS[results['status']]}: {c['problem']} to fix, "
                    f"{c['warning']} worth checking, {c['note']} nice to have.")
    body = feedback.render_pr_body(kb.name, issue["number"], changes, answers, summary_line)
    url = github.open_pull_request(
        config.REPO, branch=f"feedback/issue-{issue['number']}", base=args.base,
        title=f"Update the {kb.name} croissant (from #{issue['number']})", body=body,
        files=[str(kb.directory)],
        message=f"Update {kb.name} croissant from issue #{issue['number']}",
    )
    github.comment(config.REPO, issue["number"],
                   f"Thank you! We've prepared the change here: {url}\n\nA member of our team will review and "
                   "publish it. You'll see your croissant update on the site once it's merged.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="boulangerie", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("import-croissants", help="add croissants from a folder of <KB name>.json files")
    p.add_argument("source")
    p.add_argument("--kb-config", required=True, help="the pipeline's knowledge_bases.toml")
    p.add_argument("--skip", nargs="*", default=[], help="KB names to leave out")
    p.set_defaults(func=cmd_import_croissants)

    p = sub.add_parser("import-provenance", help="import dataset-level provenance from robo_croissant.db")
    p.add_argument("--db", required=True, help="path to the pipeline's robo_croissant.db")
    p.add_argument("--kb")
    p.set_defaults(func=cmd_import_provenance)

    p = sub.add_parser("check", help="run checks and write results/<slug>.json")
    p.add_argument("--kb")
    p.add_argument("--kbs", nargs="*", help="several KBs by name or slug")
    p.add_argument("--online", action="store_true", help="also check source pages and download links")
    p.add_argument("--mlcroissant", choices=report.MLC_MODES, default=report.MLC_CACHED,
                   help="official validator: always run, run when the croissant changed, or only reuse earlier results")
    p.add_argument("--fail-on-validator-errors", action="store_true")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("pr-summary", help="write a markdown summary of checks for some KBs")
    p.add_argument("--kbs", nargs="*", default=[])
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_pr_summary)

    p = sub.add_parser("build-site", help="build the static site")
    p.add_argument("--kb")
    p.add_argument("--out", default=str(site.DEFAULT_OUT))
    p.set_defaults(func=cmd_build_site)

    p = sub.add_parser("sync-issues", help="create/update one check issue per KB (needs GITHUB_TOKEN)")
    p.add_argument("--kb")
    p.set_defaults(func=cmd_sync_issues)

    p = sub.add_parser("autofix", help="apply automatic fixes")
    p.add_argument("--kb")
    p.add_argument("--open-prs", action="store_true")
    p.add_argument("--base", default="main")
    p.set_defaults(func=cmd_autofix)

    p = sub.add_parser("feedback", help="turn a feedback issue into a PR (run from GitHub Actions)")
    p.add_argument("--event", default=os.environ.get("GITHUB_EVENT_PATH"))
    p.add_argument("--base", default="main")
    p.set_defaults(func=cmd_feedback)

    args = parser.parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
