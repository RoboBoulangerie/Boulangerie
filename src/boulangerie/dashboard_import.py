"""INTERIM: pull dataset-level provenance (source URL + confidence) from the review dashboard.

This exists only until we import provenance from the pipeline database directly;
delete it then. The dashboard pages its field table 500 rows at a time in
natural-sort order, so per-file and per-column rows (``distribution[...]`` and
``recordSet[...]``) form long contiguous runs. We bisect over pages and skip any
stretch that is entirely inside one of those runs, which keeps BRCA Exchange
(2,400+ pages) to a few dozen requests.
"""

import html
import re
from urllib.parse import quote

import requests

DASHBOARD_URL = "https://robo-croissant-dashboard.kiluet.com"
TIMEOUT = 300

_ROW = re.compile(r'<tr data-path="([^"]*)"(.*?)</tr>', re.DOTALL)
_ATTR = re.compile(r'data-current-(value|source)="([^"]*)"')
_BADGE = re.compile(r'class="badge badge-[a-z]+">\s*([\d.]+)%')
_PAGES = re.compile(r"Page \d+ of (\d+)")


def _block(path: str) -> str | None:
    for prefix in ("distribution[", "recordSet["):
        if path.startswith(prefix):
            return prefix
    return None


def parse_page(text: str) -> tuple[list[dict], int]:
    rows = []
    for path, body in _ROW.findall(text):
        attrs = dict(_ATTR.findall(body))
        badge = _BADGE.search(body)
        rows.append({
            "path": html.unescape(path),
            "value": html.unescape(attrs.get("value", "")),
            "url": html.unescape(attrs.get("source", "")),
            "confidence": float(badge.group(1)) / 100 if badge else None,
            "source": "dashboard",
        })
    pages = _PAGES.search(text)
    return rows, int(pages.group(1)) if pages else 1


def fetch_page(session: requests.Session, kb_name: str, page: int) -> tuple[list[dict], int]:
    resp = session.get(f"{DASHBOARD_URL}/update/{quote(kb_name)}", params={"page": page}, timeout=TIMEOUT)
    resp.raise_for_status()
    rows, total = parse_page(resp.text)
    if not rows:
        raise RuntimeError(f"Dashboard page {page} for {kb_name!r} had no field rows")
    return rows, total


def dataset_level_provenance(kb_name: str, log=print) -> dict[str, dict]:
    session = requests.Session()
    first, total = fetch_page(session, kb_name, 0)
    pages = {0: first}
    if total > 1:
        pages[total - 1] = fetch_page(session, kb_name, total - 1)[0]

    stack = [(0, total - 1)] if total > 1 else []
    while stack:
        lo, hi = stack.pop()
        if hi - lo <= 1:
            continue
        end_of_lo, start_of_hi = _block(pages[lo][-1]["path"]), _block(pages[hi][0]["path"])
        if end_of_lo is not None and end_of_lo == start_of_hi:
            continue  # every page in between is inside one distribution/recordSet run
        mid = (lo + hi) // 2
        pages[mid] = fetch_page(session, kb_name, mid)[0]
        stack += [(lo, mid), (mid, hi)]

    log(f"  {kb_name}: fetched {len(pages)} of {total} pages")
    return {
        row["path"]: row
        for rows in pages.values()
        for row in rows
        if _block(row["path"]) is None and not row["path"].lstrip('"').startswith("@")
    }
