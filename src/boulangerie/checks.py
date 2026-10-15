"""Offline checks on a croissant.

Every check is a function ``croissant -> list[Finding]``. Findings are written
for KB maintainers, not Croissant experts: ``title`` and ``message`` must make
sense to someone who has never heard of Croissant. ``technical`` carries the
detail a developer needs.

Many of these checks were ported from the pipeline's ``validate_all.py``.
"""

import re
import subprocess
import sys
import tempfile
import gzip
import shutil
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from boulangerie import paths

PROBLEM = "problem"  # the croissant is wrong or unusable here
WARNING = "warning"  # probably wrong, or missing something people will want
NOTE = "note"  # nice to have

SEVERITY_ORDER = {PROBLEM: 0, WARNING: 1, NOTE: 2}
CROISSANT_1_1 = "http://mlcommons.org/croissant/1.1"

NOT_FOUND_WORDS = {
    "not found", "unknown", "n/a", "na", "none", "null", "todo", "tbd",
    "placeholder", "example", "insert here", "your text here", "",
}
_PLACEHOLDER_RE = re.compile(r"^\[.+\]$|^<.+>$")
_COLUMN_N_RE = re.compile(r"^column_\d+$", re.IGNORECASE)
_HEX_RE = re.compile(r"^[0-9a-fA-F]+$")
_TERMS_URL_RE = re.compile(
    r"[/\-_](terms|privacy|tos|conditions)(?:[/\-_.?#]|$)|/legal(?:[/.?#]|$)", re.IGNORECASE
)

EXT_TO_MIMES: dict[str, set[str]] = {
    ".csv": {"text/csv"},
    ".tsv": {"text/tab-separated-values", "text/tsv"},
    ".json": {"application/json"},
    ".jsonl": {"application/jsonlines", "application/x-ndjson"},
    ".ndjson": {"application/jsonlines", "application/x-ndjson"},
    ".parquet": {"application/parquet", "application/vnd.apache.parquet"},
    ".zip": {"application/zip"},
}
# The value we write when fixing a mismatch automatically.
EXT_TO_PREFERRED_MIME = {ext: sorted(mimes)[0] for ext, mimes in EXT_TO_MIMES.items()}
EXT_TO_PREFERRED_MIME[".tsv"] = "text/tab-separated-values"
EXT_TO_PREFERRED_MIME[".jsonl"] = "application/jsonlines"
EXT_TO_PREFERRED_MIME[".ndjson"] = "application/jsonlines"


@dataclass(frozen=True)
class Finding:
    check: str
    severity: str
    path: str
    title: str
    message: str
    technical: str = ""
    fix: dict | None = None  # {"path": ..., "value": ...} when we can fix it automatically

    def to_dict(self) -> dict:
        return asdict(self)


def is_missing(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        v = value.strip()
        return v.lower() in NOT_FOUND_WORDS or bool(_PLACEHOLDER_RE.match(v))
    if isinstance(value, (list, dict)):
        return len(value) == 0
    return False


def _as_list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


# ── Dataset-level checks ──────────────────────────────────────────────────────

# (field, severity, title, message) for dataset-level fields we look for.
CORE_FIELDS = [
    ("name", PROBLEM, "Your resource has no name",
     "We couldn't find the name of your resource. This is the first thing people see."),
    ("description", PROBLEM, "Your resource has no description",
     "We couldn't find a short description of what your resource contains."),
    ("url", PROBLEM, "No homepage link",
     "We couldn't find the main web page for your resource."),
    ("license", PROBLEM, "We couldn't find a license",
     "We couldn't find the terms under which people may reuse your data. Without a license, "
     "careful users (and many AI projects) will assume they cannot use it."),
    ("citeAs", WARNING, "We couldn't find how you'd like to be cited",
     "If you have a preferred paper or citation for your resource, adding it means people who use "
     "your data will credit you correctly."),
    ("version", NOTE, "No version or release number",
     "If your data has releases or versions, adding the current one helps people know what they downloaded."),
    ("datePublished", NOTE, "No release date",
     "Adding the date of the current release helps people judge how fresh the data is."),
]


def check_core_fields(croissant: dict) -> list[Finding]:
    findings = []
    for field_name, severity, title, message in CORE_FIELDS:
        value = croissant.get(field_name)
        if is_missing(value):
            shown = f" (it currently says {value!r})" if isinstance(value, str) and value.strip() else ""
            findings.append(Finding(
                check="missing_core_field", severity=severity, path=field_name,
                title=title, message=message + shown,
                technical=f"Dataset-level `{field_name}` is missing or a placeholder.",
            ))
    if croissant.get("conformsTo") != CROISSANT_1_1:
        findings.append(Finding(
            check="conforms_to", severity=PROBLEM, path="conformsTo",
            title="The file doesn't say which Croissant version it uses",
            message="This is a technical label that tools need. We can fix it automatically.",
            technical=f"`conformsTo` should be `{CROISSANT_1_1}`, got {croissant.get('conformsTo')!r}.",
            fix={"path": "conformsTo", "value": CROISSANT_1_1},
        ))
    return findings


def check_license_is_terms_page(croissant: dict) -> list[Finding]:
    findings = []
    for i, value in enumerate(_as_list(croissant.get("license"))):
        url = value.get("url") if isinstance(value, dict) else value
        if isinstance(url, str) and url.startswith(("http://", "https://")) and _TERMS_URL_RE.search(url):
            path = "license" if not isinstance(croissant.get("license"), list) else f"license[{i}]"
            findings.append(Finding(
                check="license_is_terms_page", severity=WARNING, path=path,
                title="The license points to a terms-of-use page",
                message="The license link goes to a website terms-of-use or privacy page, which covers using "
                        "the website, not reusing the data. If your data has a license (for example CC BY 4.0), "
                        "please tell us which one.",
                technical=f"License URL {url} looks like a terms/privacy/legal page.",
            ))
    return findings


def check_placeholders(croissant: dict) -> list[Finding]:
    """Placeholder text in dataset-level string values (not distributions/record sets)."""
    findings = []
    core = {f for f, *_ in CORE_FIELDS}
    for key, value in croissant.items():
        if key in core or key.startswith("@") or not isinstance(value, str):
            continue
        if is_missing(value):
            findings.append(Finding(
                check="placeholder_value", severity=WARNING, path=key,
                title=f"`{key}` looks unfinished",
                message=f"The value {value!r} looks like a placeholder rather than real information.",
                technical=f"Dataset-level `{key}` = {value!r}.",
            ))
    return findings


# ── Distribution (file) checks ────────────────────────────────────────────────

def check_distributions(croissant: dict) -> list[Finding]:
    findings = []
    dists = _as_list(croissant.get("distribution"))
    if not dists:
        findings.append(Finding(
            check="no_files", severity=PROBLEM, path="distribution",
            title="No downloadable files listed",
            message="We didn't find any files people can download. If your data is downloadable, "
                    "tell us where the download page is.",
            technical="`distribution` is empty or missing.",
        ))
        return findings

    id_counts = Counter(d.get("@id") for d in dists if d.get("@id"))
    url_counts = Counter(d.get("contentUrl") for d in dists if d.get("contentUrl"))
    reported_ids: set[str] = set()
    reported_urls: set[str] = set()

    for i, d in enumerate(dists):
        prefix = f"distribution[{i}]"
        dist_id = d.get("@id")
        url = d.get("contentUrl")
        fmt = d.get("encodingFormat")
        dtype = d.get("@type")

        if dtype not in ("cr:FileObject", "cr:FileSet"):
            findings.append(Finding(
                check="wrong_distribution_type", severity=PROBLEM, path=f"{prefix}.@type",
                title="A file entry has the wrong type",
                message="A file entry is labelled in a way Croissant tools won't recognise.",
                technical=f"`@type` must be cr:FileObject or cr:FileSet, got {dtype!r}.",
            ))

        if dist_id and id_counts[dist_id] > 1 and dist_id not in reported_ids:
            reported_ids.add(dist_id)
            findings.append(Finding(
                check="duplicate_distribution_id", severity=PROBLEM, path=f"{prefix}.@id",
                title="Two file entries share the same ID",
                message="Some files can't be told apart because they share an internal ID.",
                technical=f"`@id` {dist_id!r} is used by {id_counts[dist_id]} distributions.",
            ))

        if url and url_counts[url] > 1 and url not in reported_urls and "containedIn" not in d:
            reported_urls.add(url)
            findings.append(Finding(
                check="repeated_content_url", severity=WARNING, path=f"{prefix}.contentUrl",
                title="Several files point to the same download link",
                message="Several different files are listed with the same download link, so we probably "
                        "don't have the right link for each one.",
                technical=f"contentUrl {url} is shared by {url_counts[url]} distributions.",
            ))

        if url and dtype == "cr:FileObject" and "containedIn" not in d \
                and not url.startswith(("http://", "https://", "ftp://")):
            findings.append(Finding(
                check="relative_content_url", severity=WARNING, path=f"{prefix}.contentUrl",
                title="A file has an incomplete download link",
                message="A file's download link is not a full web address, so nobody outside can download it.",
                technical=f"contentUrl {url!r} is not an absolute URL.",
            ))

        if isinstance(url, str) and isinstance(fmt, str):
            url_path = url.split("?")[0].lower()
            for ext, ok in EXT_TO_MIMES.items():
                if url_path.endswith(ext) and fmt not in ok:
                    findings.append(Finding(
                        check="format_extension_mismatch", severity=WARNING, path=f"{prefix}.encodingFormat",
                        title="A file's type doesn't match its name",
                        message=f"A file ending in {ext} is described as {fmt}. We can fix this automatically.",
                        technical=f"{url} ends in {ext} but encodingFormat is {fmt!r}.",
                        fix={"path": f"{prefix}.encodingFormat", "value": EXT_TO_PREFERRED_MIME[ext]},
                    ))
                    break

        for algo, length in (("md5", 32), ("sha256", 64)):
            value = d.get(algo)
            if value is not None and not (isinstance(value, str) and len(value) == length and _HEX_RE.match(value)):
                findings.append(Finding(
                    check="invalid_checksum", severity=WARNING, path=f"{prefix}.{algo}",
                    title="A file has a malformed checksum",
                    message="A file's checksum (used to confirm a download is complete) isn't in the right format.",
                    technical=f"{algo} must be {length} hex characters, got {value!r}.",
                ))
    return findings


def check_compressed_without_inner_file(croissant: dict) -> list[Finding]:
    dists = _as_list(croissant.get("distribution"))
    contained = {
        ref.get("@id")
        for d in dists
        for ref in _as_list(d.get("containedIn"))
        if isinstance(ref, dict)
    }
    findings = []
    for i, d in enumerate(dists):
        url = (d.get("contentUrl") or "").lower().split("?")[0]
        if url.endswith((".zip", ".tar.gz", ".tgz")) and d.get("@id") not in contained:
            findings.append(Finding(
                check="archive_without_inner_files", severity=NOTE, path=f"distribution[{i}]",
                title="We don't describe what's inside an archive",
                message="One of your downloads is a compressed archive, and we haven't described the files inside it.",
                technical=f"{d.get('contentUrl')} has no FileObject/FileSet with containedIn pointing at it.",
            ))
    return findings


# ── Record set (table/column) checks ──────────────────────────────────────────

def _walk_fields(fields, prefix: str):
    """Yield (path, field) for fields and their subFields (e.g. GeoCoordinates parts).

    ``prefix`` is the list's own path, e.g. ``recordSet[3].field``.
    """
    for j, f in enumerate(_as_list(fields)):
        path = f"{prefix}[{j}]"
        yield path, f
        if f.get("subField"):
            yield from _walk_fields(f["subField"], f"{path}.subField")


def check_record_sets(croissant: dict) -> list[Finding]:
    dist_ids = {d.get("@id") for d in _as_list(croissant.get("distribution"))}
    findings = []
    for i, rs in enumerate(_as_list(croissant.get("recordSet"))):
        for prefix, f in _walk_fields(rs.get("field"), f"recordSet[{i}].field"):
            source = f.get("source")
            if not source and f.get("subField"):
                continue  # a parent field takes its values from its subFields
            name = f.get("name") or ""
            if not source:
                findings.append(Finding(
                    check="field_missing_source", severity=WARNING, path=prefix,
                    title="A column isn't linked to a file",
                    message="We describe a column but don't say which file it comes from.",
                    technical=f"Field {name!r} has no `source`.",
                ))
            else:
                ref = source.get("fileObject") or source.get("fileSet") or {}
                ref_id = ref.get("@id") if isinstance(ref, dict) else None
                if ref_id and ref_id not in dist_ids:
                    findings.append(Finding(
                        check="broken_source_reference", severity=PROBLEM, path=f"{prefix}.source",
                        title="A column points to a file that isn't listed",
                        message="We describe a column from a file that doesn't appear in the list of files.",
                        technical=f"Field {name!r} references {ref_id!r}, which is not a distribution @id.",
                    ))
            if _COLUMN_N_RE.match(name):
                findings.append(Finding(
                    check="placeholder_column_name", severity=WARNING, path=f"{prefix}.name",
                    title="A column has a made-up name",
                    message="We couldn't read the real column names for one of your files, so we used "
                            "generic names like column_1.",
                    technical=f"Field name {name!r} looks generated.",
                ))
        if "data" in rs and rs.get("field") and any(f.get("source") for f in _as_list(rs.get("field"))):
            findings.append(Finding(
                check="inline_data_rows", severity=WARNING, path=f"recordSet[{i}].data",
                title="Sample rows are labelled as the full data",
                message="Some example rows are labelled as if they were the complete data.",
                technical="Record set has both file-backed fields and inline `data`; use `examples` instead.",
            ))
    return findings


# Query parameters that carry credentials. A link with one of these gives every reader the key.
CREDENTIAL_PARAMS = {"apikey", "api_key", "api-key", "key", "token", "access_token", "auth", "password",
                     "secret", "client_secret", "sig", "signature"}


def strip_credentials(url: str) -> str:
    """The URL without credential query parameters (``?apikey=...``)."""
    parts = urlsplit(url)
    kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k.lower() not in CREDENTIAL_PARAMS]
    return urlunsplit(parts._replace(query=urlencode(kept)))


def check_credentials_in_links(croissant: dict) -> list[Finding]:
    """No value anywhere in the croissant may carry a credential in a URL."""
    findings = []
    for path, value in paths.leaves(croissant):
        if not isinstance(value, str) or "?" not in value or not value.startswith(("http://", "https://", "ftp://")):
            continue
        params = {k.lower() for k, _ in parse_qsl(urlsplit(value).query, keep_blank_values=True)}
        leaked = sorted(params & CREDENTIAL_PARAMS)
        if leaked:
            findings.append(Finding(
                check="credential_in_link", severity=PROBLEM, path=path,
                title="A link contains a password or access key",
                message="One of the links includes an access key, which anyone reading the croissant can copy. "
                        "We'll remove it; if it's yours, please replace the key.",
                technical=f"Query parameter(s) {', '.join(leaked)} in {strip_credentials(value)} (value withheld).",
                fix={"path": path, "value": strip_credentials(value)},
            ))
    return findings


TABULAR_FORMATS = {"text/csv", "text/tab-separated-values", "text/tsv", "text/vcf", "application/parquet",
                   "application/vnd.apache.parquet", "application/jsonlines", "application/x-ndjson"}
# For compressed files, whose type only says "gzip", the name tells us what's inside.
TABULAR_SUFFIXES = (".csv", ".tsv", ".vcf", ".parquet", ".jsonl", ".ndjson")
_COMPRESSION = re.compile(r"\.(gz|bz2|xz|zip)$", re.IGNORECASE)


def looks_tabular(dist: dict) -> bool:
    """Is this file a table, by its type or (for compressed files) its name?"""
    formats = _as_list(dist.get("encodingFormat"))
    if any(isinstance(f, str) and f.lower() in TABULAR_FORMATS for f in formats):
        return True
    name = _COMPRESSION.sub("", str(dist.get("name") or dist.get("contentUrl") or "")).lower()
    return dist.get("@type") == "cr:FileObject" and name.endswith(TABULAR_SUFFIXES)


def _table_sources(fields) -> set[str]:
    ids = set()
    for f in _as_list(fields):
        source = f.get("source") or {}
        for key in ("fileObject", "fileSet"):
            ref = source.get(key)
            if isinstance(ref, dict) and ref.get("@id"):
                ids.add(ref["@id"])
        ids |= _table_sources(f.get("subField"))
    return ids


def check_tables_described(croissant: dict) -> list[Finding]:
    """Files that hold tables should say what their columns are."""
    described = {i for rs in _as_list(croissant.get("recordSet")) for i in _table_sources(rs.get("field"))}
    findings = []
    for i, d in enumerate(_as_list(croissant.get("distribution"))):
        if looks_tabular(d) and d.get("@id") not in described:
            findings.append(Finding(
                check="table_not_described", severity=WARNING, path=f"distribution[{i}]",
                title="We didn't describe the columns of a table",
                message="This file holds a table, but we didn't list its columns, so people can't tell what's in it "
                        "without downloading it. We'll add them on our side.",
                technical=f"{d.get('name') or d.get('contentUrl')} ({d.get('encodingFormat')}) has no recordSet "
                          "whose fields read from it.",
            ))
    return findings


OFFLINE_CHECKS = [
    check_core_fields,
    check_license_is_terms_page,
    check_placeholders,
    check_distributions,
    check_compressed_without_inner_file,
    check_record_sets,
    check_tables_described,
    check_credentials_in_links,
]


def run_offline_checks(croissant: dict) -> list[Finding]:
    findings = [f for check in OFFLINE_CHECKS for f in check(croissant)]
    return sort_findings(findings)


def sort_findings(findings: list[Finding]) -> list[Finding]:
    return sorted(findings, key=lambda f: (SEVERITY_ORDER[f.severity], f.check, f.path))


# ── Official validator ────────────────────────────────────────────────────────

_MLC_SECTION = re.compile(r"Found the following \d+ (error|warning)\(s\)")
_MLC_ITEM = re.compile(r"^\s+-\s+(.*)$")
_MLC_LOG_LINE = re.compile(r"^[IWEF]\d{4} ")  # absl log prefix, e.g. "W1013 20:51:49.699622 ..."
_MLC_IGNORED_WARNINGS = (
    "is recommended, but does not exist",  # duplicates our own missing-field checks
    "Version doesn't follow MAJOR.MINOR.PATCH",  # KBs use real release names like 2026-07-15; not theirs to change
)


def is_useful_mlcroissant_finding(f: Finding) -> bool:
    return not (f.check == "mlcroissant_warning" and any(s in f.technical for s in _MLC_IGNORED_WARNINGS))


def parse_mlcroissant_output(output: str) -> list[Finding]:
    """Turn mlcroissant's log into findings, dropping warnings that aren't useful to maintainers."""
    findings = []
    kind = None
    for line in output.splitlines():
        section = _MLC_SECTION.search(line)
        if section:
            kind = section.group(1)
            continue
        if _MLC_LOG_LINE.match(line):
            kind = None
            continue
        item = _MLC_ITEM.match(line)
        if kind and item:
            text = item.group(1).strip()
            if kind == "warning" and any(s in text for s in _MLC_IGNORED_WARNINGS):
                continue
            findings.append(Finding(
                check="mlcroissant_error" if kind == "error" else "mlcroissant_warning",
                severity=PROBLEM if kind == "error" else NOTE,
                path="",
                title="The official Croissant checker reported an error" if kind == "error"
                else "The official Croissant checker has a suggestion",
                message="This is a technical issue in the file. We'll fix it on our side.",
                technical=text,
            ))
    return findings


def run_mlcroissant(croissant_path: Path, timeout: int = 1800) -> list[Finding]:
    """Run the MLCommons validator. A crash or timeout is reported, not hidden."""
    exe = Path(sys.executable).parent / "mlcroissant"
    with tempfile.TemporaryDirectory() as tmp:
        target = croissant_path
        if croissant_path.suffix == ".gz":
            target = Path(tmp) / "croissant.json"
            with gzip.open(croissant_path, "rb") as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
        try:
            result = subprocess.run(
                [str(exe), "validate", "--jsonld", str(target)],
                capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return [Finding(
                check="mlcroissant_timeout", severity=WARNING, path="",
                title="The official Croissant checker didn't finish",
                message="Your croissant is very large, and the official checker ran out of time.",
                technical=f"mlcroissant validate exceeded {timeout}s.",
            )]
    output = result.stdout + result.stderr
    findings = parse_mlcroissant_output(output)
    if result.returncode != 0 and not any(f.check == "mlcroissant_error" for f in findings):
        tail = "\n".join(output.strip().splitlines()[-5:])
        findings.append(Finding(
            check="mlcroissant_error", severity=PROBLEM, path="",
            title="The official Croissant checker reported an error",
            message="This is a technical issue in the file. We'll fix it on our side.",
            technical=f"mlcroissant exited with {result.returncode}: {tail}",
        ))
    return findings
