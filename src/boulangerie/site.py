"""Build the static site that maintainers visit to pick up their croissant."""

import datetime
import gzip
import json
import os
import shutil
from pathlib import Path

from jinja2 import Environment, PackageLoader, select_autoescape

from boulangerie import checks, config, feedback, report, review, summary
from boulangerie.catalog import REPO_ROOT, KnowledgeBase, read_confidence, read_croissant, read_provenance
from boulangerie.checks import Finding
from boulangerie.online import MATCH_EXACT, MATCH_NORMALIZED

STATIC_DIR = Path(__file__).parent / "static"
VERIFIED = {MATCH_EXACT, MATCH_NORMALIZED}


def _env() -> Environment:
    env = Environment(
        loader=PackageLoader("boulangerie", "templates"),
        autoescape=select_autoescape(["html", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.globals.update(
        repo=config.REPO,
        status_labels=report.STATUS_LABELS,
        severity_words=report.SEVERITY_WORDS,
    )
    env.filters["display"] = summary.display_value
    env.filters["link_dois"] = summary.link_dois
    env.filters["thousands"] = lambda n: f"{n:,}"
    env.filters["size"] = lambda n: summary.human_size(n) if n else ""
    return env


def source_for(provenance: dict[str, dict], field: str) -> dict:
    """Provenance for a field, or for its first part when it's a list or object (``license[0]``, ``creator.name``)."""
    if field in provenance:
        return provenance[field]
    parts = sorted(p for p in provenance if p.startswith((f"{field}[", f"{field}.")))
    return provenance[parts[0]] if parts else {}


def describe_fields(croissant: dict, provenance: dict[str, dict], grounding: dict[str, str], missing: dict[str, str],
                    fix_url=lambda label: "") -> list[dict]:
    """``missing`` maps a missing field to the severity of that finding; ``fix_url(label)`` links to its form."""
    rows = []
    for field, label, explanation in summary.FIELD_EXPLANATIONS:
        value = croissant.get(field)
        source = source_for(provenance, field)
        is_missing = field in missing
        if value is None and not is_missing:
            continue
        rows.append({
            "field": field,
            "label": label,
            "explanation": explanation,
            "value": "" if is_missing else summary.display_value(value),
            "is_url": isinstance(value, str) and value.startswith(("http://", "https://")),
            "missing": is_missing,
            "missing_severity": missing.get(field, ""),
            "source_url": source.get("url", ""),
            "from_maintainer": source.get("source") == "maintainer",
            "confidence": source.get("confidence"),
            "verified": grounding.get(field) in VERIFIED,
            "fix_url": fix_url(label) if label in feedback.DETAILS else "",
            "unverified": grounding.get(field) in ("partial", "none"),
            # We tried, but couldn't read the source page (JavaScript, bot wall, error) or had no source to check.
            "unchecked": grounding.get(field) in ("unverified", "no_source"),
        })
    return rows


def _publish_croissant(kb: KnowledgeBase, dest: Path) -> int:
    """Copy the croissant to the site as indented JSON, so people can read the file they download.

    Croissants are stored indented; big ones are gzipped in the repo and served uncompressed.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    if kb.croissant_path.suffix == ".gz":
        with gzip.open(kb.croissant_path, "rb") as fin, open(dest, "wb") as fout:
            shutil.copyfileobj(fin, fout)
    else:
        shutil.copyfile(kb.croissant_path, dest)
    return dest.stat().st_size


DETAILS_CHUNK = 500  # files per details file; opening a file loads only its chunk


def _compact(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def write_browse_data(review_data: dict, kb_dir: Path) -> None:
    """Split the review data so pages load only what they show.

    - ``files-tree.json``: the folder tree plus one short row per file (path, kind, size, needs a look, rank), for
      browsing and searching; PGS Catalog's is about 5 MB before compression
    - ``files/<n>.json``: full details for files n*500 to n*500+499, fetched when one of them is opened
    - ``tables.json``: the kinds of tables and their layouts
    """
    kinds = review_data["kinds"]
    files = review_data["files"]
    rows = [[f["path"], f["kind"], f["size"], int(f["look"]), f["rank"]] for f in files]
    (kb_dir / "files-tree.json").write_text(_compact({
        "tree": review_data["tree"], "files": rows, "chunk": DETAILS_CHUNK, "counts": review_data["counts"],
    }), encoding="utf-8")
    details_dir = kb_dir / "files"
    details_dir.mkdir(parents=True, exist_ok=True)
    for start in range(0, max(len(files), 1), DETAILS_CHUNK):
        chunk = []
        for f in files[start:start + DETAILS_CHUNK]:
            detail = {k: v for k, v in f.items() if k not in ("table_kinds", "rank", "order")}
            detail["table_kind_names"] = [kinds[k]["title"] for k in f.get("table_kinds", [])]
            chunk.append(detail)
        (details_dir / f"{start // DETAILS_CHUNK}.json").write_text(_compact(chunk), encoding="utf-8")
    (kb_dir / "tables.json").write_text(_compact({
        "kinds": kinds, "tables": review_data["tables"], "counts": review_data["counts"],
    }), encoding="utf-8")


def build_site(catalog: list[KnowledgeBase], out_dir: Path, results_dir: Path = report.RESULTS_DIR) -> None:
    env = _env()
    # GitHub Pages lets browsers cache files for 10 minutes. Pages ask for the script and data with this build's id,
    # so a new build never mixes with an old cached script or data file.
    env.globals["build"] = os.environ.get("GITHUB_SHA", "")[:12] or datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    if out_dir.exists():
        shutil.rmtree(out_dir)
    (out_dir / "kb").mkdir(parents=True)
    shutil.copytree(STATIC_DIR, out_dir / "static")
    (out_dir / ".nojekyll").touch()

    cards = []
    for kb in catalog:
        croissant = read_croissant(kb)
        provenance = read_provenance(kb)
        results = report.load_results(kb, results_dir)
        if results is None:
            raise FileNotFoundError(f"No check results for {kb.name}; run `boulangerie check` first.")
        stats = summary.summarize(croissant)
        missing = {
            ex["path"]: g["severity"]
            for g in results["groups"] if g["check"] == "missing_core_field" for ex in g["examples"]
        }
        size = _publish_croissant(kb, out_dir / "kb" / kb.slug / "croissant.json")
        findings = checks.run_offline_checks(croissant) + [Finding(**f) for f in results.get("online_findings", [])]
        review_data = review.build_review(croissant, read_confidence(kb), findings,
                                          link_checks=results.get("links", {}).get("sampled"),
                                          checked_at=results.get("checked_at", ""))
        write_browse_data(review_data, out_dir / "kb" / kb.slug)
        forms = {key: report.feedback_url(kb, config.REPO, key) for key in
                 ("file-problem", "table-problem", "missing-files", "extra-files", "download-page")}
        (out_dir / "kb" / kb.slug / "review").mkdir()
        (out_dir / "kb" / kb.slug / "review" / "index.html").write_text(env.get_template("review.html.j2").render(
            root="../../../",
            kb=kb,
            counts=review_data["counts"],
            forms=forms,
        ), encoding="utf-8")

        page = env.get_template("kb.html.j2").render(
            root="../../",
            kb=kb,
            croissant=croissant,
            fields=describe_fields(croissant, provenance, results.get("grounding", {}), missing,
                                   fix_url=lambda label: report.feedback_url(kb, config.REPO, "fix-detail", detail=label)),
            stats=stats,
            results=results,
            download_size=summary.human_size(size),
            feedback_url=report.feedback_url(kb, config.REPO),
            page_url=f"{config.SITE_URL.rstrip('/')}/kb/{kb.slug}/",
            croissant_url=f"{config.SITE_URL.rstrip('/')}/kb/{kb.slug}/croissant.json",
            has_provenance=bool(provenance),
            review_counts=review_data["counts"],
            forms=forms,
        )
        (out_dir / "kb" / kb.slug / "index.html").write_text(page, encoding="utf-8")
        cards.append({
            "kb": kb,
            "status": results["status"],
            "counts": results["counts"],
            "file_count": stats["file_count"],
            "description": summary.display_value(croissant.get("description", "")),
        })

    (out_dir / "index.html").write_text(env.get_template("index.html.j2").render(root="", cards=cards), encoding="utf-8")
    (out_dir / "about").mkdir()
    (out_dir / "about" / "index.html").write_text(
        env.get_template("about.html.j2").render(root="../"), encoding="utf-8")
    # A machine-readable index, handy for anyone harvesting the croissants.
    (out_dir / "catalog.json").write_text(json.dumps([
        {"name": c["kb"].name, "slug": c["kb"].slug, "status": c["status"],
         "croissant": f"kb/{c['kb'].slug}/croissant.json"} for c in cards
    ], indent=2) + "\n", encoding="utf-8")


DEFAULT_OUT = REPO_ROOT / "_site"
