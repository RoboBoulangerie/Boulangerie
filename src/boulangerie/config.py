"""Where the repo and site live.

In GitHub Actions the repo comes from ``GITHUB_REPOSITORY``, so the same code works in a sandbox copy of the repo.
``BOULANGERIE_REPO`` and ``BOULANGERIE_SITE_URL`` override it, e.g. for a custom domain.
"""

import os

REPO = os.environ.get("BOULANGERIE_REPO") or os.environ.get("GITHUB_REPOSITORY") or "RoboBoulangerie/Boulangerie"


def _pages_url(repo: str) -> str:
    owner, name = repo.split("/", 1)
    return f"https://{owner.lower()}.github.io/{name}/"


SITE_URL = os.environ.get("BOULANGERIE_SITE_URL") or _pages_url(REPO)
