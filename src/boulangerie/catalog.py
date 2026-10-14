"""The catalog of knowledge bases and their croissants.

Each knowledge base lives in ``kbs/<slug>/`` with:

- ``kb.toml``: who the KB is (name, homepage, grant, GitHub maintainers)
- ``croissant.json`` or ``croissant.json.gz``: the Croissant metadata itself;
  large files are stored gzipped to stay under GitHub's file size limits
- ``provenance.json`` (optional): where each dataset-level value was found
"""

import gzip
import json
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
KBS_DIR = REPO_ROOT / "kbs"

# Croissants larger than this are stored gzipped (GitHub warns at 50 MB, rejects at 100 MB).
GZIP_THRESHOLD_BYTES = 10_000_000


@dataclass(frozen=True)
class KnowledgeBase:
    slug: str
    name: str
    title: str
    homepage: str
    directory: Path
    grant_number: str = ""
    maintainers: tuple[str, ...] = field(default_factory=tuple)

    @property
    def croissant_path(self) -> Path:
        gz = self.directory / "croissant.json.gz"
        return gz if gz.exists() else self.directory / "croissant.json"

    @property
    def provenance_path(self) -> Path:
        return self.directory / "provenance.json"


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def load_kb(directory: Path) -> KnowledgeBase:
    with open(directory / "kb.toml", "rb") as f:
        meta = tomllib.load(f)
    return KnowledgeBase(
        slug=directory.name,
        name=meta["name"],
        title=meta.get("title", meta["name"]),
        homepage=meta.get("homepage", ""),
        directory=directory,
        grant_number=meta.get("grant_number", ""),
        maintainers=tuple(meta.get("maintainers", [])),
    )


def load_catalog(kbs_dir: Path = KBS_DIR) -> list[KnowledgeBase]:
    kbs = [load_kb(d) for d in sorted(kbs_dir.iterdir()) if (d / "kb.toml").exists()]
    return sorted(kbs, key=lambda kb: kb.name.lower())


def find_kb(catalog: list[KnowledgeBase], name_or_slug: str) -> KnowledgeBase:
    wanted = name_or_slug.strip()
    for kb in catalog:
        if wanted in (kb.slug, kb.name) or slugify(wanted) == kb.slug:
            return kb
    raise KeyError(f"No knowledge base named {wanted!r}")


def read_croissant(kb: KnowledgeBase) -> dict:
    path = kb.croissant_path
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def write_croissant(kb: KnowledgeBase, croissant: dict, gzip_threshold: int = GZIP_THRESHOLD_BYTES) -> Path:
    """Write the croissant back, gzipping it if it is over the size threshold."""
    text = json.dumps(croissant, indent=2, ensure_ascii=False) + "\n"
    data = text.encode("utf-8")
    plain = kb.directory / "croissant.json"
    gz = kb.directory / "croissant.json.gz"
    if len(data) > gzip_threshold:
        # mtime=0 keeps the gzip bytes stable so unchanged content makes no git diff.
        with open(gz, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, filename="") as f:
            f.write(data)
        plain.unlink(missing_ok=True)
        return gz
    plain.write_bytes(data)
    gz.unlink(missing_ok=True)
    return plain


def read_provenance(kb: KnowledgeBase) -> dict[str, dict]:
    """Map of croissant path -> {"value", "url", "confidence", "source"}."""
    if not kb.provenance_path.exists():
        return {}
    with open(kb.provenance_path, encoding="utf-8") as f:
        return {entry["path"]: entry for entry in json.load(f)}


def write_provenance(kb: KnowledgeBase, provenance: dict[str, dict]) -> None:
    entries = [provenance[p] for p in sorted(provenance)]
    kb.provenance_path.write_text(json.dumps(entries, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
