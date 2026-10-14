# 🥐 Boulangerie

Boulangerie is where biomedical knowledge bases pick up their **croissant**: a
[Croissant](https://mlcommons.org/croissant/) metadata file that describes their data
so that search engines, data catalogs and AI tools can find it and load it correctly.

- **Site:** https://roboboulangerie.github.io/Boulangerie/ (one plain-language page per knowledge base)
- **Corrections:** maintainers use the "Suggest a correction" form; a bot turns it into a pull request
- **Checks:** every croissant is checked on each change and weekly; results show on the site and in one
  `Croissant check: <name>` issue per knowledge base

The croissants are drafted by [robo-croissant-pipeline](https://github.com/RoboBoulangerie/robo-croissant-pipeline).
See [DESIGN.md](DESIGN.md) for how the pieces fit together and why.

## Layout

```
kbs/<slug>/kb.toml              who the knowledge base is, and its maintainers' GitHub usernames
kbs/<slug>/croissant.json[.gz]  the croissant (gzipped when over 10 MB)
kbs/<slug>/provenance.json      where each dataset-level value was found
kbs/<slug>/confidence.json      how sure the pipeline was about each file and table
src/boulangerie/                checks, site builder, feedback handling, CLI
.github/workflows/              publish, PR checks, corrections → PRs, automatic fixes
```

## Working locally

Everything runs in a `uv` environment.

```bash
uv sync
```

Run the checks and build the site:

```bash
uv run boulangerie check
```

```bash
uv run boulangerie build-site
```

Then open `_site/index.html`, or serve it:

```bash
python3 -m http.server 8765 --directory _site
```

`check` options:

- `--online` also re-reads the pages each value came from and tries a sample of download links
- `--mlcroissant changed` also runs the official MLCommons validator, only for croissants that changed since
  the last run (`always` forces it). It is slow and memory-hungry on the largest croissants (BRCA Exchange takes
  about 9 minutes and 8.6 GB).
- `--kb <name>` checks one knowledge base

Run the tests:

```bash
uv run pytest
```

## Adding or updating croissants

From a folder of `<KB name>.json` files and the pipeline's `knowledge_bases.toml`:

```bash
uv run boulangerie import-croissants ../croissant-files --kb-config ../robo-croissant-pipeline/knowledge_bases.toml
```

Provenance (where each dataset-level value came from) and per-file/per-table confidence are imported from the
pipeline's database. Values a maintainer confirmed through the correction form are kept:

```bash
uv run boulangerie import-provenance --db ~/Downloads/robo_croissant.db
```

Collapse repeated archive members into FileSets (run after importing croissants, before importing provenance):

```bash
uv run boulangerie collapse-filesets --dry-run
```

## One-time GitHub setup

1. **Settings → Pages → Source:** GitHub Actions.
2. **Settings → Actions → General → Workflow permissions:** allow GitHub Actions to create and approve pull requests.
3. Create the `feedback` label (the correction form applies it, and only existing labels are applied):

   ```bash
   gh label create feedback --repo RoboBoulangerie/Boulangerie --color 0e8a16 --description "Correction from a maintainer"
   ```
