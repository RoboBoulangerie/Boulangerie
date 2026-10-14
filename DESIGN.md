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
- `boulangerie import-provenance --db robo_croissant.db` reads the dataset-level rows of the database's `kb_links`
  table: about 450 rows across all KBs, out of 4.4 million. The database is 3.3 GB, almost all of it per-file and
  per-column rows, indexes, and run history, so we never host it. The import opens the database read-only, keeps
  the `reviewed`/`auto_reviewed` flags, and never overwrites values a maintainer confirmed.
- When a maintainer corrects a value, its provenance becomes `{"source": "maintainer", "url": <the issue>}`, and the
  site shows "Confirmed by your team".

### Reviewing files and tables

Maintainers are the judges of whether we described their data correctly, not just its name and license. Each KB
has a "Did we describe your data correctly?" page (`kb/<slug>/review/`), and the KB page previews the same
structure. Both show files and tables the way their owners organize them (`organize.py`), not by file type.

- **Files, in their folders.** The tree comes from the download URLs, and archive contents sit inside their
  archive. To keep big KBs readable:
  - Chains of single folders collapse into one step (`ftp.ebi.ac.uk › pub › databases › spot › pgs`).
  - Sibling files or folders with the same name pattern (digit runs and month names as `*`) are grouped:
    PGS's 5,450 `PGS######` folders, CIViC's 133 monthly release folders.
  - Folders with many loose files are grouped into families by name (CTD's `CTD_chem_*`, GlyGen's species),
    and copies of one dataset in several formats merge into one entry (`CTD_anatomy` · CSV, TSV, XML).
  - Folders open on demand, and long lists render 100 at a time.
- **Tables, by kind.** Tables belong to one family if they have the same columns, or if their files share a path
  pattern: PGS's 17,091 tables are 13 kinds, and Gene Ontology's 2,760 are 12. Within a family, each column
  layout lists only the columns that differ from what the whole family shares. PGS's harmonized scoring files
  come in 203 layouts over 6 shared columns. Each layout lists the files that use it.
- **Search, or "only ones that need a look,"** switches to a flat list of matching files or table kinds,
  most-likely-wrong first, with each file's folder path shown.
- **"Needs a look"** is ranked by check findings (worst first), then the pipeline's confidence below 0.9, then
  unreviewed items. It rolls up, so a folder or table kind shows how many of its items need a look. Pipeline
  confidence barely varies at this level, so the checks do most of the ranking.
- **Each file** shows what we found, why it's flagged, the parts we weren't sure about in plain words, and a
  "Something wrong with this?" link that pre-fills the correction form. Item-level corrections go to a person
  (`needs-review`).
- **Three standing questions at the top:** did we miss files, is anything listed that shouldn't be, and is there
  a better download page.
- **Data:** one compact `review.json` per KB holds the file details, the tree and the table families. The largest
  is BioPortal at 24 MB, mostly its ontologies' long column IRIs; Pages compresses it in transit.
- `confidence.json` holds one summary per file and table from the database: lowest confidence, how many values
  have been reviewed, and the least certain values. The database stores rows by position (`distribution[12]`),
  so positions are mapped to `@id`s through the database's own copy of each croissant (checked to match ours for
  all 20). FileSets made by `collapse-filesets` take the lowest confidence among their former members.

### FileSets

`collapse-filesets` replaces repeated members of one archive (same format, same name pattern with digit runs as
`*`, same columns) with one `cr:FileSet` and one record set. A group is collapsed only when its glob matches exactly
that group among everything known to be in the archive. Remote files outside an archive are never grouped, because
Croissant 1.1 can only glob inside a container. The minimum group size is 20, because small families like MSigDB's
C1-C8 collections are distinct products to their owners. It took FlyBase from 121,916 listed files to 2,202.

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
  re-run only when the file changes. Its "is recommended" warnings duplicate our own checks, and its warning that versions aren't MAJOR.MINOR.PATCH
  flags KBs' real release names (`2026-07-15`, `FB2026_02`), so both are dropped. All 20 croissants pass it with
  no errors; the first full run took 1 h 40 min, and later runs take about a minute.
- **Online** (`online.py`):
  - **Grounding:** re-fetches the page each dataset-level value came from and checks that the text really appears
    there (exact, then ignoring spacing, case, quotes and dashes, then sentence-by-sentence). This addresses
    RoboBoulangerie/robo-croissant-pipeline#5. A page we can't read is reported as "couldn't verify", never as a
    mismatch. That covers pages that fail to load, JavaScript apps (Alliance), bot walls and CAPTCHAs (CTD), and
    403s (BioPortal). Citation mismatches are only a note, because citations are routinely reformatted (PubMed, BibTeX).
  - **Links:** a deterministic sample of 40 download links per KB. FlyBase lists 122k files, and checking them all
    weekly would be slow and unkind to their servers. A 401 or 403 is reported as "needs a login or key" (BioPortal
    downloads need an API key), not as a broken link.

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
  entirely? Boulangerie only depends on its database, for provenance.
- **Past releases:** each croissant should describe one release, but several include past ones (BRCA Exchange,
  PGS Catalog, ICTV, PhenX, maybe ClinPGx). How to handle stale links in older croissants is under discussion in
  RoboBoulangerie/robo-croissant-pipeline#8.
- **Large remote file collections:** PGS Catalog's 16k scoring files and similar can't be globbed in Croissant 1.1;
  left as they are for now (RoboBoulangerie/robo-croissant-pipeline#7).
- **Ownership:** once a KB is happy with its croissant, do they host it themselves and we mirror it, or do we
  stay the source of truth?
