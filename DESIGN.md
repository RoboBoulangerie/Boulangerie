# Design

## Goals

- Knowledge base (KB) maintainers can find their croissant, understand it without knowing what Croissant is,
  and take it home.
- The croissants get better over time: automatic checks find problems, and maintainers correct what only they
  know (license, citation, access rules).
- Everything lives in GitHub. No server to run.

## Who uses it

- **KB maintainers**: mostly not Croissant experts. They read the site, and suggest corrections through a GitHub
  issue form. A GitHub account is required to send corrections.
- **Our team**: reviews and merges the pull requests, and imports new croissants from the pipeline.

## Pieces

```
robo-croissant-pipeline ──► kbs/<slug>/ (croissant + provenance) ──► checks ──► site (GitHub Pages)
                                    ▲                                  │
                                    │                                  ├─► one "Croissant check" issue per KB
         maintainer ── issue form ──┴── bot opens PR ◄─────────────────┘
```

### Storage: one folder per KB

- `kb.toml`, `croissant.json[.gz]`, `provenance.json`.
- Croissants over 10 MB are stored gzipped (7 of 20 today; the largest, BRCA Exchange, is 84 MB raw). GitHub warns
  at 50 MB and rejects files over 100 MB, and Git LFS files can't be served by Pages. The gzip is written with a
  fixed timestamp so unchanged content gives no git diff. The site serves the uncompressed file, so maintainers
  never see a `.gz`.
- `kb.toml` copies the name, title, homepage and grant number from the pipeline's `knowledge_bases.toml`. It
  deliberately leaves out the reviewer `notes` (internal opinions) and the PI names.
- `maintainers` (GitHub usernames) are @-mentioned in the check issue. They are empty until KBs tell us who
  they are.

### Provenance

- The croissant file says nothing about where each value came from. The pipeline records that in
  `persistent_fields.json` (path, value, source URL, confidence), and the review dashboard holds it in its database.
- We keep only **dataset-level** provenance (name, description, license, ...). It's what maintainers need to see,
  and the per-file and per-column rows are hundreds of thousands of entries for the big KBs.
- **Stopgap:** `boulangerie import-dashboard-provenance` reads it from the dashboard's review pages. It bisects over
  the dashboard's pages to skip the long `distribution[...]` and `recordSet[...]` runs, so 13 requests cover BRCA's
  2,434 pages. Replace this with an import from the pipeline database, then delete `dashboard_import.py`.
- When a maintainer corrects a value, its provenance becomes `{"source": "maintainer", "url": <the issue>}`, and the
  site shows "Confirmed by your team".

### Checks

All checks produce `Finding`s with a plain-language `title`/`message` for maintainers, a `technical` detail for us,
and optionally an automatic `fix`. Severities:

- **Needs fixing** (`problem`): wrong or unusable, e.g. no license, a broken file reference, validator errors.
- **Worth checking** (`warning`): probably wrong, e.g. wording that isn't on the source page, broken download links.
- **Nice to have** (`note`): e.g. no version or release date.

There are three groups:

- **Offline** (`checks.py`): fast structural and content checks, mostly ported from the pipeline's `validate_all.py`
  so the two agree.
- **Official validator** (`mlcroissant validate`): the authority on spec compliance, but slow and memory-hungry
  on big croissants (BRCA Exchange: about 9 minutes and 8.6 GB). Results are cached by the croissant's SHA-256 and
  re-run only when the file changes. Its "is recommended" warnings duplicate our own checks and are dropped.
- **Online** (`online.py`):
  - **Grounding:** re-fetches the page each dataset-level value came from and checks that the text really appears
    there (exact, then ignoring spacing, case, quotes and dashes, then sentence-by-sentence). This addresses
    RoboBoulangerie/robo-croissant-pipeline#5. A page that won't load is reported as "couldn't verify", never as a mismatch.
  - **Links:** a deterministic sample of 40 download links per KB. FlyBase lists 122k files, and checking them all
    weekly would be slow and unkind to their servers.

Thousands of identical findings (one per file) are grouped into one entry with a count and five examples, so the
issue and the page stay readable.

### GitHub workflows

| Workflow | When | What |
|---|---|---|
| `publish.yml` | push to `main`, Mondays, manual | Checks everything (online too), updates the check issues, builds and deploys the site. |
| `pr-check.yml` | every PR | Runs the tests, checks the croissants the PR touches, and comments a summary table. Fails only on official validator errors, so a missing license doesn't block a merge. |
| `feedback.yml` | an issue with the `feedback` label is opened or edited | Parses the form, edits the croissant and provenance, runs the checks, opens a PR that closes the issue, and replies on the issue with the link. Editing the issue updates the same PR. |
| `autofix.yml` | Mondays, manual | One PR per KB for mechanical fixes (e.g. a `.tsv` file labelled `text/csv`). |

- **One check issue per KB**, titled `Croissant check: <name>` and labelled `croissant-check` and `kb:<slug>`. It is
  updated in place, closed when there's nothing to fix or check, and reopened if problems come back.
  This serves as the GitHub "dashboard": filter issues by `croissant-check`.
- **Corrections go through a PR, never straight to `main`,** so a person reviews every change. Maintainers see
  exactly what will change before it's published.
- **Security:** the issue body is read from the event file by Python and never interpolated into shell commands.
  PRs opened by the bot with `GITHUB_TOKEN` don't trigger other workflows, so `feedback.yml` runs the checks
  itself and puts the results in the PR description.

### Site

- Static HTML from Jinja templates (`build-site`), deployed by Actions. There is no JavaScript framework; one small
  script filters the KB list.
- Pages:
  - **Home:** what a croissant is in two sentences, three steps, and a searchable list with a status for each KB.
  - **What is a croissant?:** a plain-language explainer and FAQ.
  - **One page per KB:**
    - download button
    - "What your croissant says about you": each dataset-level value with what it means, where we found it, and
      whether it matches that page
    - files grouped by kind, with counts and sizes
    - a sample of tables and columns
    - the check results in plain language, with technical details folded away
    - "What do I do with my croissant?"
- `catalog.json` lists every croissant for anyone harvesting them.

## Open questions

- **The dashboard:** keep it as the internal curation tool and export into Boulangerie, or move review into GitHub
  entirely? Boulangerie doesn't depend on it except for the stopgap provenance import.
- **Very large croissants:** BRCA Exchange describes 4,839 files and 296k columns, and FlyBase describes 122k files.
  These are hard for maintainers to review, and probably need the pipeline to model repeated files as `cr:FileSet`
  patterns.
- **Ownership:** once a KB is happy with its croissant, do they host it themselves and we mirror it, or do we
  stay the source of truth?
