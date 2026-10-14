"""Small GitHub REST helpers used by the workflows. Uses ``GITHUB_TOKEN``."""

import os
import subprocess

import requests

API = "https://api.github.com"
CHECK_LABEL = "croissant-check"
FEEDBACK_LABEL = "feedback"
NEEDS_HUMAN_LABEL = "needs-review"


def _session() -> requests.Session:
    token = os.environ["GITHUB_TOKEN"]
    s = requests.Session()
    s.headers.update({
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    return s


def _check(resp: requests.Response) -> requests.Response:
    if not resp.ok:
        raise RuntimeError(f"GitHub API {resp.request.method} {resp.url} -> {resp.status_code}: {resp.text[:500]}")
    return resp


def ensure_label(repo: str, name: str, color: str, description: str = "") -> None:
    s = _session()
    resp = s.get(f"{API}/repos/{repo}/labels/{requests.utils.quote(name, safe='')}")
    if resp.status_code == 404:
        _check(s.post(f"{API}/repos/{repo}/labels", json={"name": name, "color": color, "description": description}))
    else:
        _check(resp)


def kb_label(slug: str) -> str:
    return f"kb:{slug}"


def find_check_issue(repo: str, slug: str) -> dict | None:
    s = _session()
    resp = _check(s.get(f"{API}/repos/{repo}/issues", params={
        "labels": f"{CHECK_LABEL},{kb_label(slug)}", "state": "all", "per_page": 10,
    }))
    issues = [i for i in resp.json() if "pull_request" not in i]
    issues.sort(key=lambda i: (i["state"] != "open", -i["number"]))
    return issues[0] if issues else None


def upsert_check_issue(repo: str, slug: str, kb_name: str, body: str, all_good: bool) -> str:
    """Keep exactly one check issue per KB. Returns what happened, for the log."""
    s = _session()
    title = f"Croissant check: {kb_name}"
    existing = find_check_issue(repo, slug)
    if existing is None:
        if all_good:
            return "no issue needed"
        ensure_label(repo, CHECK_LABEL, "c5a572", "Automatic croissant check results")
        ensure_label(repo, kb_label(slug), "ededed", kb_name)
        resp = _check(s.post(f"{API}/repos/{repo}/issues", json={
            "title": title, "body": body, "labels": [CHECK_LABEL, kb_label(slug)],
        }))
        return f"created #{resp.json()['number']}"
    number = existing["number"]
    state = "closed" if all_good else "open"
    if existing["body"] == body and existing["state"] == state:
        return f"#{number} unchanged"
    _check(s.patch(f"{API}/repos/{repo}/issues/{number}", json={"title": title, "body": body, "state": state}))
    return f"updated #{number} ({state})"


def add_labels(repo: str, number: int, labels: list[str]) -> None:
    for name in labels:
        ensure_label(repo, name, "fbca04", "A person needs to read this")
    _check(_session().post(f"{API}/repos/{repo}/issues/{number}/labels", json={"labels": labels}))


def comment(repo: str, number: int, body: str) -> None:
    _check(_session().post(f"{API}/repos/{repo}/issues/{number}/comments", json={"body": body}))


def open_pull_request(repo: str, branch: str, base: str, title: str, body: str, files: list[str], message: str) -> str:
    """Commit ``files`` on a new branch, push it, and open a PR. Returns the PR URL.

    Afterwards the working tree is back on the branch we started from.
    """
    start = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], check=True,
                           capture_output=True, text=True).stdout.strip()
    subprocess.run(["git", "checkout", "-B", branch], check=True)
    subprocess.run(["git", "add", "-A", "--", *files], check=True)
    subprocess.run(["git", "commit", "-m", message], check=True)
    subprocess.run(["git", "push", "--force", "origin", branch], check=True)
    subprocess.run(["git", "checkout", start], check=True)
    s = _session()
    existing = _check(s.get(f"{API}/repos/{repo}/pulls", params={
        "head": f"{repo.split('/')[0]}:{branch}", "state": "open",
    })).json()
    if existing:
        _check(s.patch(f"{API}/repos/{repo}/pulls/{existing[0]['number']}", json={"title": title, "body": body}))
        return existing[0]["html_url"]
    resp = _check(s.post(f"{API}/repos/{repo}/pulls", json={"title": title, "body": body, "head": branch, "base": base}))
    return resp.json()["html_url"]
