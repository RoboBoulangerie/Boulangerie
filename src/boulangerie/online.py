"""Checks that need the network.

- Grounding: does each dataset-level value (name, description, citation, ...)
  actually appear on the page we say it came from?
- Links: do the download links work? Some KBs list 100k+ files, so we check a
  fixed-size, deterministic sample.
"""

import html
import random
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser

import requests

from boulangerie.checks import NOTE, WARNING, Finding

HTTP_TIMEOUT = 20
USER_AGENT = "Boulangerie croissant checker (+https://github.com/RoboBoulangerie/Boulangerie)"

# Dataset-level text fields whose wording should come straight from the KB's own pages.
GROUNDED_FIELDS = ("name", "description", "citeAs", "conditionsOfAccess")

MATCH_EXACT = "exact"
MATCH_NORMALIZED = "normalized"  # same words, differing only in spacing, case, quotes or dashes
MATCH_PARTIAL = "partial"  # most sentences found
MATCH_NONE = "none"


class _TextExtractor(HTMLParser):
    _SKIP = {"script", "style", "noscript", "template"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data):
        if not self._skip_depth:
            self.parts.append(data)


def page_text(body: str, content_type: str = "text/html") -> str:
    if "html" not in content_type:
        return body
    parser = _TextExtractor()
    parser.feed(body)
    return " ".join(parser.parts)


_TRANSLATE = str.maketrans({
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", " ": " ",
})


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", html.unescape(text)).translate(_TRANSLATE)
    return re.sub(r"\s+", " ", text).strip().casefold()


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", text) if len(s) > 20]


_BOT_WALL_PHRASES = ("verify you are a human", "captcha", "are you a robot", "checking your browser",
                     "access denied", "just a moment")


def unreadable_reason(text: str) -> str | None:
    """Why a fetched page can't be compared against, or None if it looks like real content."""
    t = normalize(text)
    if any(p in t for p in _BOT_WALL_PHRASES):
        return "blocks automated visitors"
    if len(t) < 400 or "enable javascript" in t or "javascript is required" in t:
        return "is built with JavaScript, so our checker can't read its text"
    return None


def match_value(value: str, text: str) -> str:
    """How closely does ``value`` appear in the page ``text``?"""
    if value in text:
        return MATCH_EXACT
    v, t = normalize(value), normalize(text)
    if v and v in t:
        return MATCH_NORMALIZED
    sentences = _sentences(v)
    if len(sentences) >= 2 and sum(s in t for s in sentences) / len(sentences) >= 0.5:
        return MATCH_PARTIAL
    return MATCH_NONE


def fetch_text(url: str) -> str:
    """Fetch a page and return its visible text. Raises on HTTP or network errors."""
    resp = requests.get(url, timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    return page_text(resp.text, resp.headers.get("Content-Type", ""))


def _text_value(value) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return " ".join(value)
    return None


def check_grounding(croissant: dict, provenance: dict[str, dict]) -> tuple[list[Finding], dict[str, str]]:
    """Returns findings and a map of field -> match level (used by the site to show a ✓)."""
    to_check = []
    for field in GROUNDED_FIELDS:
        value = _text_value(croissant.get(field))
        source = provenance.get(field, {})
        if value is None or source.get("source") == "maintainer":
            continue
        to_check.append((field, value, source.get("url", "")))

    urls = {url for _, _, url in to_check if url.startswith(("http://", "https://"))}
    pages: dict[str, str | Exception] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        for url, result in zip(urls, pool.map(_fetch_or_error, urls)):
            pages[url] = result

    findings, levels = [], {}
    for field, value, url in to_check:
        if not url.startswith(("http://", "https://")):
            levels[field] = "no_source"
            findings.append(Finding(
                check="grounding_no_source", severity=NOTE, path=field,
                title=f"We didn't record where the {field} came from",
                message="We can't show you the page this came from. Please check it reads correctly.",
                technical=f"No http(s) source URL recorded for `{field}` (got {url!r}).",
            ))
            continue
        page = pages[url]
        if isinstance(page, Exception):
            levels[field] = "unverified"
            findings.append(Finding(
                check="grounding_unverified", severity=NOTE, path=field,
                title=f"We couldn't re-open the page the {field} came from",
                message=f"We tried to double-check this against {url} but couldn't load the page.",
                technical=f"{type(page).__name__}: {page}",
            ))
            continue
        level = match_value(value, page)
        reason = unreadable_reason(page) if level == MATCH_NONE else None
        if reason:
            levels[field] = "unverified"
            findings.append(Finding(
                check="grounding_unverified", severity=NOTE, path=field,
                title=f"We couldn't re-read the page the {field} came from",
                message=f"{url} {reason}. Please check this reads correctly.",
                technical=f"Page {reason} ({len(normalize(page))} characters of text).",
            ))
            continue
        levels[field] = level
        if level in (MATCH_PARTIAL, MATCH_NONE) and field == "citeAs":
            # Citations are routinely reformatted (PubMed, BibTeX, journal style), so a mismatch is weak evidence.
            findings.append(Finding(
                check="grounding_citation_reformatted", severity=NOTE, path=field,
                title="The citation is formatted differently from your page",
                message=f"We couldn't find this exact citation on {url}. Citations are often reformatted, so this "
                        "may be fine, but please check it's the one you want people to use.",
                technical=f"Match level {level!r} for `citeAs` against {url}.",
            ))
        elif level in (MATCH_PARTIAL, MATCH_NONE):
            findings.append(Finding(
                check="grounding_mismatch", severity=WARNING, path=field,
                title=f"The {field} doesn't match your web page word for word",
                message=f"We couldn't find this text on {url}. It may have been reworded, or come from a "
                        "different page. Please check it's accurate.",
                technical=f"Match level {level!r} for `{field}` against {url}. Pages rendered by JavaScript "
                          "can also cause this.",
            ))
    return findings, levels


def _fetch_or_error(url: str) -> str | Exception:
    try:
        return fetch_text(url)
    except requests.RequestException as e:  # reported as a finding by the caller
        return e


def _head_status(url: str) -> int | Exception:
    try:
        resp = requests.head(url, timeout=HTTP_TIMEOUT, allow_redirects=True, headers={"User-Agent": USER_AGENT})
        if resp.status_code in (403, 405, 501):  # some servers refuse HEAD; confirm with GET
            resp = requests.get(url, timeout=HTTP_TIMEOUT, stream=True, headers={"User-Agent": USER_AGENT})
            resp.close()
        return resp.status_code
    except requests.RequestException as e:
        return e


def check_links(croissant: dict, sample_size: int = 40, seed: str = "") -> tuple[list[Finding], dict]:
    """Check a deterministic sample of download links. Returns findings and a summary."""
    candidates = [
        (i, d["contentUrl"]) for i, d in enumerate(croissant.get("distribution") or [])
        if isinstance(d.get("contentUrl"), str) and d["contentUrl"].startswith(("http://", "https://"))
        and "containedIn" not in d
    ]
    rng = random.Random(seed or croissant.get("name", ""))
    sample = candidates if len(candidates) <= sample_size else rng.sample(candidates, sample_size)
    with ThreadPoolExecutor(max_workers=10) as pool:
        statuses = list(pool.map(_head_status, [url for _, url in sample]))

    findings = []
    broken = 0
    for (i, url), status in zip(sample, statuses):
        if status in (401, 403):
            broken += 1
            findings.append(Finding(
                check="link_needs_login", severity=NOTE, path=f"distribution[{i}].contentUrl",
                title="Some downloads need a login or key",
                message="We couldn't download some files without signing in or an access key. If that's expected, "
                        "it helps to say so under access conditions.",
                technical=f"HTTP {status} for {url}",
            ))
        elif isinstance(status, Exception) or status >= 400:
            broken += 1
            detail = f"HTTP {status}" if isinstance(status, int) else f"{type(status).__name__}: {status}"
            findings.append(Finding(
                check="broken_link", severity=WARNING, path=f"distribution[{i}].contentUrl",
                title="A download link doesn't work",
                message=f"We couldn't download {url}. The file may have moved, or need a login.",
                technical=detail,
            ))
    summary = {"total": len(candidates), "checked": len(sample), "broken": broken}
    return findings, summary
