import json
import shutil
from pathlib import Path

import pytest

from boulangerie.catalog import load_catalog

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def kbs_dir(tmp_path) -> Path:
    """A writable copy of the fixture catalog."""
    target = tmp_path / "kbs"
    shutil.copytree(FIXTURES / "kbs", target)
    return target


@pytest.fixture
def tiny_kb(kbs_dir):
    return load_catalog(kbs_dir)[0]


@pytest.fixture
def tiny_croissant() -> dict:
    return json.loads((FIXTURES / "kbs" / "tiny-kb" / "croissant.json").read_text())
