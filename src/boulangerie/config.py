"""Where the repo and site live. Override with environment variables when testing a fork."""

import os

REPO = os.environ.get("BOULANGERIE_REPO", "RoboBoulangerie/Boulangerie")
SITE_URL = os.environ.get("BOULANGERIE_SITE_URL", "https://roboboulangerie.github.io/Boulangerie/")
