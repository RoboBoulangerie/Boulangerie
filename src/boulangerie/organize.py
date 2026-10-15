"""Organize a croissant's files and tables the way their owners think about them.

Files are arranged by where they live: the folders in their download URLs, with
archive contents inside their archive. Tables are grouped by type: every
record set with the same columns is one table type, used by one or more files.

To keep big KBs readable:

- a chain of folders with only one child each is shown as one step
  (``ftp.ebi.ac.uk › pub › databases › spot › pgs``)
- many sibling files or folders with the same name pattern (digit runs and
  month names as ``*``) are grouped, so PGS Catalog's 5,450 ``PGS######``
  folders and CIViC's monthly release folders are one entry each
"""

import datetime
import fnmatch
import re
from collections import Counter, defaultdict
from urllib.parse import unquote, urlsplit

from boulangerie.summary import parse_size

GROUP_AT = 5  # siblings that look alike are grouped once there are this many
# Digit runs and month names vary between otherwise identical names (PGS000001, 01-Apr-2026).
_VARYING = re.compile(r"\d+|(?<![A-Za-z])(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)(?![a-z])", re.IGNORECASE)


def name_pattern(name: str) -> str:
    return _VARYING.sub("*", name)


def _fileset_segments(dist: dict) -> list[str]:
    """Where a FileSet sits inside its container: its glob's folders, or one entry if it lists many paths."""
    includes = dist.get("includes")
    if isinstance(includes, list) and len(includes) == 1:
        includes = includes[0]
    if isinstance(includes, str):
        return [s for s in includes.split("/") if s]
    return [dist.get("name") or f"{len(includes or []):,} listed files"]


def location(dist: dict, by_id: dict[str, dict]) -> list[str]:
    """Folder path and file name for a file: host, then URL path; archive members go inside their archive."""
    parent = (dist.get("containedIn") or {}).get("@id")
    url = dist.get("contentUrl") or ""
    if parent in by_id and parent != dist.get("@id"):
        if dist.get("@type") == "cr:FileSet" and not url:
            inner = _fileset_segments(dist)
        else:
            inner = [s for s in url.split("!/", 1)[-1].split("/") if s] or [dist.get("name") or "?"]
        return location(by_id[parent], by_id) + inner
    parts = urlsplit(url)
    if not parts.netloc:
        name = dist.get("name") or url or dist.get("@id") or "?"
        return ["Files without a link", name]
    segments = [unquote(s) for s in parts.path.split("/") if s] or [dist.get("name") or parts.netloc]
    if parts.query:
        segments[-1] += "?" + parts.query
    return [parts.netloc] + segments


# Tree nodes are dicts:
#   {"name", "type": "folder" | "archive" | "file" | "group", "children": [...], "files", "size", "look",
#    "file": index into the review file list (files and archives), "of": "files" | "folders" (groups)}


def _new(name: str, node_type: str) -> dict:
    return {"name": name, "type": node_type, "children": {}, "files": 0, "size": 0, "look": 0}


def build_file_tree(dists: list[dict], look: list[bool]) -> dict:
    """``look[i]`` says whether file i needs a look. Returns the root node."""
    by_id = {d.get("@id"): d for d in dists}
    root = _new("", "folder")
    archive_ids = {(d.get("containedIn") or {}).get("@id") for d in dists} & set(by_id)
    for i, d in enumerate(dists):
        path = location(d, by_id)
        node = root
        for depth, segment in enumerate(path):
            last = depth == len(path) - 1
            child = node["children"].get(segment)
            if child is None:
                child = node["children"][segment] = _new(segment, "file" if last else "folder")
            node = child
        node["file"] = i
        if d.get("@id") in archive_ids:
            node["type"] = "archive"
    _finish(root, dists, look)
    return root


def _finish(node: dict, dists: list[dict], look: list[bool]) -> None:
    """Turn child dicts into lists, total up counts, collapse chains and group look-alike siblings."""
    children = list(node["children"].values())
    for child in children:
        _finish(child, dists, look)
    if "file" in node:
        d = dists[node["file"]]
        size = parse_size(d.get("contentSize"))
        is_set = d.get("@type") == "cr:FileSet"
        node["files"] = 1 + sum(c["files"] for c in children)
        node["size"] = (size or 0) + sum(c["size"] for c in children)
        node["look"] = int(look[node["file"]]) + sum(c["look"] for c in children)
        if is_set:
            node["type"] = "fileset"
    else:
        node["files"] = sum(c["files"] for c in children)
        node["size"] = sum(c["size"] for c in children)
        node["look"] = sum(c["look"] for c in children)
    node["children"] = _group_flat(_group_siblings(sorted(children, key=_sort_key)))
    # A folder whose only child is a folder is shown as one step.
    while node["type"] == "folder" and len(node["children"]) == 1 and node["children"][0]["type"] == "folder":
        only = node["children"][0]
        node["name"] = f"{node['name']} › {only['name']}" if node["name"] else only["name"]
        node["children"] = only["children"]


_DATE_FORMATS = [
    (re.compile(r"(\d{1,2})-([A-Za-z]{3})-(\d{4})"), "%d-%b-%Y"),  # CIViC: 01-Apr-2016
    (re.compile(r"(\d{4})-(\d{2})-(\d{2})"), "%Y-%m-%d"),  # 2026-07-15
    (re.compile(r"release-(\d{2})-(\d{2})-(\d{2})"), "%m-%d-%y"),  # BRCA Exchange: release-07-27-20
]


def name_date(name: str) -> datetime.date | None:
    """The date in a name like '01-Apr-2016' or 'release-07-27-20', if there is one."""
    for pattern, fmt in _DATE_FORMATS:
        m = pattern.search(name)
        if m:
            try:
                return datetime.datetime.strptime("-".join(m.groups()), fmt).date()
            except ValueError:
                continue
    return None


def _sort_key(node: dict):
    # Folders before files; dated names in date order (01-May-2015 before 01-Apr-2016); then natural order
    # (file_2 before file_10).
    leaf = node["type"] in ("file", "fileset")
    date = name_date(node["name"])
    natural = [(0, int(t), "") if t.isdigit() else (1, 0, t.lower()) for t in re.split(r"(\d+)", node["name"])]
    return (leaf, date is None, date or datetime.date.min, natural)


def _group_siblings(children: list[dict]) -> list[dict]:
    buckets: dict[tuple, list[dict]] = defaultdict(list)
    keys = []
    for c in children:
        if c["type"] in ("file", "fileset"):
            key = ("file", name_pattern(c["name"]))
        elif c["type"] == "folder":
            key = ("folder", name_pattern(c["name"]))
        else:
            key = ("other", id(c))
        buckets[key].append(c)
        keys.append(key)
    out, done = [], set()
    for key in keys:
        if key in done:
            continue
        done.add(key)
        members = buckets[key]
        if len(members) < GROUP_AT or key[0] == "other":
            out.extend(members)
            continue
        # A pattern that's only wildcards ("*-*-*" for dated folders) says nothing; show the range instead.
        label = key[1] if re.sub(r"[*\-_. ›]", "", key[1]) else f"{members[0]['name']} … {members[-1]['name']}"
        out.append({
            "name": label,
            "type": "group",
            "of": "files" if key[0] == "file" else "folders",
            "count": len(members),
            "children": members,
            "files": sum(m["files"] for m in members),
            "size": sum(m["size"] for m in members),
            "look": sum(m["look"] for m in members),
        })
    return out


def relative_paths(dists: list[dict]) -> dict[str, str]:
    """Each file's location below the folder all of a KB's files share, e.g. 'go-cams › R-HSA-1.json'."""
    by_id = {d.get("@id"): d for d in dists}
    paths = {d.get("@id"): location(d, by_id) for d in dists}
    folders = [p[:-1] for p in paths.values()]
    shared = 0
    while folders and all(len(f) > shared for f in folders) and len({f[shared] for f in folders}) == 1:
        shared += 1
    return {i: " › ".join(p[shared:]) for i, p in paths.items()}


def build_table_families(record_sets: list[dict], file_paths: dict[str, str], look: list[bool]) -> list[dict]:
    """Group tables the way people think of them: a family of files, then each column layout in it.

    Tables are in the same family if they have the same columns, or if their files have the same path pattern
    (digit runs and months as ``*``): GO's 2,130 GO-CAM models are one family, and so are PGS Catalog's scoring
    files even though they come in 146 column layouts. Within a family, each layout lists only the columns that
    differ from what the whole family shares (``common``).

    Returns families, those needing a look first, then the most widely used:
    {"name", "common": [[col, type]], "layouts": [{"tables", "files", "columns", "extra", "look"}], "files", "look"}
    """
    # A layout is the columns and how they're read. The recorded kind of value (dataType) is left out: the pipeline
    # guesses it from a file's first rows, so identical files can disagree (BindingDB's 1,907 surflex CSVs came out
    # as 7 "layouts" that differed only in whether Ki/IC50 were Text, Integer or Float).
    signatures = [_layout_signature(rs.get("field") or []) for rs in record_sets]
    sources = [sorted(_sources(rs.get("field") or [])) for rs in record_sets]

    # Union-find over tables: join tables with the same layout or the same file path pattern.
    parent = list(range(len(record_sets)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    first_with: dict = {}
    for i in range(len(record_sets)):
        keys = [("layout", signatures[i])] + [("path", name_pattern(file_paths.get(s, s))) for s in sources[i]]
        for key in keys:
            if key in first_with:
                parent[find(i)] = find(first_with[key])
            else:
                first_with[key] = i

    members_of: dict[int, list[int]] = defaultdict(list)
    for i in range(len(record_sets)):
        members_of[find(i)].append(i)

    out = []
    for members in members_of.values():
        layouts: dict[tuple, list[int]] = defaultdict(list)
        for i in members:
            layouts[signatures[i]].append(i)
        family_layouts = []
        for tables in layouts.values():
            columns, types_vary = _columns_with_types([record_sets[i].get("field") or [] for i in tables])
            family_layouts.append({
                "tables": tables,
                "files": _natural_sorted({file_paths.get(s, s) for i in tables for s in sources[i]}),
                "columns": columns,
                "types_vary": types_vary,
                "look": sum(look[i] for i in tables),
            })
        column_sets = [{c[0] for c in layout["columns"]} for layout in family_layouts]
        common_names = set.intersection(*column_sets)
        common = [c for c in family_layouts[0]["columns"] if c[0] in common_names]
        for layout in family_layouts:
            layout["extra"] = [c for c in layout["columns"] if c[0] not in common_names]
        family_layouts.sort(key=lambda l: (-l["look"], -len(l["tables"])))
        files = _natural_sorted({f for l in family_layouts for f in l["files"]})
        name = _family_name(record_sets, members, files)
        example = _representative(files)
        out.append({
            "name": name,
            "title": friendly_name(name, example),
            "example": example,
            "common": common,
            "layouts": family_layouts,
            "files": len(files),
            "tables": len(members),
            "look": sum(l["look"] for l in family_layouts),
        })
    return sorted(_merge_same_name(out), key=lambda f: (f["look"] == 0, -f["tables"], f["name"].lower()))


def _representative(files: list[str]) -> str:
    """An example file to show for a family: the newest, if the files are dated, else the first."""
    if not files:
        return ""
    dated = [(name_date(f), f) for f in files]
    dated = [(d, f) for d, f in dated if d]
    return max(dated)[1] if dated else files[0]


def _merge_same_name(families: list[dict]) -> list[dict]:
    """Families that would show the same name (e.g. each BioPortal ontology's CSV) are shown as one."""
    by_name: dict[str, list[dict]] = defaultdict(list)
    for f in families:
        by_name[f["name"]].append(f)
    merged = []
    for name, group in by_name.items():
        if len(group) == 1:
            merged.append(group[0])
            continue
        layouts = [l for f in group for l in f["layouts"]]
        common_names = set.intersection(*({c[0] for c in l["columns"]} for l in layouts))
        for l in layouts:
            l["extra"] = [c for c in l["columns"] if c[0] not in common_names]
        layouts.sort(key=lambda l: (-l["look"], -len(l["tables"])))
        merged.append({
            "name": name,
            "title": group[0]["title"],
            "example": group[0]["example"],
            "common": [c for c in layouts[0]["columns"] if c[0] in common_names],
            "layouts": layouts,
            "files": sum(f["files"] for f in group),
            "tables": sum(f["tables"] for f in group),
            "look": sum(f["look"] for f in group),
        })
    return merged


def _layout_signature(fields: list[dict]) -> tuple:
    """Columns and how each is read, in order, without the recorded kind of value."""
    return tuple(
        (f.get("name"), repr((f.get("source") or {}).get("extract")), repr((f.get("source") or {}).get("transform")),
         _layout_signature(f.get("subField") or []))
        for f in fields
    )


def _columns_with_types(tables_fields: list[list[dict]]) -> tuple[list[list[str]], list[dict]]:
    """Columns of tables that share a layout, with each column's kind of value.

    When the tables disagree about a column's kind of value, the column shows them all ("Integer or Text") and
    ``types_vary`` says how many tables had each: [{"column", "types": [[type, count], ...]}].
    """
    per_table = [_columns(fields) for fields in tables_fields]
    columns, types_vary = [], []
    for position, (name, _) in enumerate(per_table[0]):
        counts = Counter(cols[position][1] for cols in per_table if position < len(cols))
        ranked = [t for t, _ in counts.most_common()]
        if len(ranked) > 1:
            types_vary.append({"column": name, "types": [[t, n] for t, n in counts.most_common()]})
            shown = ", ".join(t or "unknown" for t in ranked[:-1]) + f" or {ranked[-1] or 'unknown'}"
        else:
            shown = ranked[0] if ranked else ""
        columns.append([name, shown])
    return columns, types_vary


def _columns(fields: list[dict], prefix: str = "") -> list[list[str]]:
    cols = []
    for f in fields:
        name = f"{prefix}{f.get('name', '')}"
        data_type = f.get("dataType")
        if isinstance(data_type, list):
            data_type = " / ".join(str(t) for t in data_type)
        cols.append([name, str(data_type or "").split(":", 1)[-1]])
        cols += _columns(f.get("subField") or [], prefix=f"{name}.")
    return cols


def _natural_sorted(names) -> list[str]:
    return sorted(names, key=lambda n: [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", n)])


def _sources(fields: list[dict]) -> set[str]:
    ids = set()
    for f in fields:
        source = f.get("source") or {}
        for key in ("fileObject", "fileSet"):
            ref = source.get(key)
            if isinstance(ref, dict) and ref.get("@id"):
                ids.add(ref["@id"])
        ids |= _sources(f.get("subField") or [])
    return ids


def _family_name(record_sets: list[dict], members: list[int], files: list[str]) -> str:
    """Name a family after its files: their shared path pattern, else what their paths share, else an example.

    The name is a glob. Files spread over a few places are named by their file names plus the places
    ("*.csv in data › surflex › * and rwd › … › surflex.tar › *"). Otherwise, if the glob doesn't match
    every file, the name says how many are named differently.
    """
    if len(files) == 1:
        return files[0]
    if not files:
        rs = record_sets[members[0]]
        return re.sub(r"^Records from ", "", rs.get("name") or rs.get("@id") or "Table")
    candidate, misfits = _best_pattern(files)
    # A glob that starts with a wildcard folder ("* › *_X › *.csv") hides where the files are; name the places.
    if candidate and not misfits and candidate.split(" › ")[0] != "*":
        return candidate
    spread = _name_by_places(files)
    if spread:
        return spread
    if candidate and not misfits:
        return candidate
    if not candidate or misfits * 2 > len(files):
        return f"{files[0]} and {len(files) - 1:,} similar files"
    return f"{candidate} (and {misfits:,} {'file' if misfits == 1 else 'files'} named differently)"


def _best_pattern(names: list[str], allow_extension_only: bool = False) -> tuple[str, int]:
    """The glob that matches the most names (most specific first), and how many names it misses."""
    patterns = Counter(name_pattern(n) for n in names)
    top = patterns.most_common(1)[0][0]
    repeated = [p for p, count in patterns.items() if count > 1]  # one-off names (e.g. "nightly") aside
    candidates = [c for c in (top, _wordwise_pattern(list(patterns)), _common_affixes(list(patterns)),
                              _common_affixes(repeated) if len(repeated) > 1 else "", _common_affixes(names))
                  if c and "*" in c]
    if allow_extension_only:
        extensions = {n.rsplit(".", 1)[-1] for n in names if "." in n}
        if len(extensions) == 1:
            candidates.append(f"*.{extensions.pop()}")
    if not candidates:
        return "", len(names)
    misfit_counts = [sum(not fnmatch.fnmatchcase(n, c) for n in names) for c in candidates]
    best = min(range(len(candidates)), key=lambda k: misfit_counts[k])
    return candidates[best], misfit_counts[best]


def _name_by_places(paths: list[str]) -> str:
    """'*.csv in A and B' for files that share a name pattern but sit in two or three different places."""
    split = [p.split(" › ") for p in paths]
    if any(len(parts) < 2 for parts in split):
        return ""
    folders = Counter(" › ".join(parts[:-1]) for parts in split)
    # Many sibling folders (one per entry, like 1A29_TFP) count as one place: their parent, plus " › *".
    by_parent: dict[str, set[str]] = defaultdict(set)
    for folder in folders:
        parent, _, last = folder.rpartition(" › ")
        by_parent[parent].add(last)
    places = Counter()
    for parts in split:
        folder = " › ".join(parts[:-1])
        parent, _, _ = folder.rpartition(" › ")
        places[f"{parent} › *" if len(by_parent[parent]) > 3 else folder] += 1
    if not 2 <= len(places) <= 3:
        return ""
    base, misfits = _best_pattern([parts[-1] for parts in split], allow_extension_only=True)
    if not base or misfits:
        return ""
    names = [p.lstrip(" ›") or "the top folder" for p, _ in places.most_common()]
    return f"{base} in {', '.join(names[:-1])} and {names[-1]}"


_WORDS = re.compile(r"([-_. ›]+)")


def _wordwise_pattern(names: list[str]) -> str:
    """'*_protein_xref_*.csv' from names that split into the same number of words: differing words become '*'."""
    by_length: dict[int, list[list[str]]] = defaultdict(list)
    for n in names:
        parts = _WORDS.split(n)
        by_length[len(parts)].append(parts)
    split = max(by_length.values(), key=len)  # the most common shape, if it covers at least half
    if len(split) < len(names) / 2:
        return ""
    merged = [parts[0] if len(set(parts)) == 1 else "*" for parts in zip(*split)]
    fixed = [w for w in merged[::2] if w != "*"]
    if not fixed or sum(len(w) for w in fixed) < 4:
        return ""
    return re.sub(r"\*(?:[-_.]\*)+", "*", "".join(merged))


def _cuts_word(prefix: str, name: str) -> bool:
    """Does ``prefix`` end partway through a word of ``name``? A capital letter starts a new word (CamelCase)."""
    if not prefix or len(name) <= len(prefix):
        return False
    last, following = prefix[-1], name[len(prefix)]
    return last.isalnum() and following.isalnum() and not (following.isupper() and not last.isupper())


def trim_partial_word(prefix: str, names: list[str]) -> str:
    """'arabidopsis_prote' -> 'arabidopsis_'; 'Summary-AcceptedA' -> 'Summary-Accepted'."""
    if not any(_cuts_word(prefix, n) for n in names):
        return prefix
    return re.sub(r"[A-Z]?[a-z0-9]*$|[A-Z]+$", "", prefix, count=1) if prefix[-1].isalnum() else prefix


def _common_affixes(names: list[str]) -> str:
    """'*' between the longest shared prefix and suffix, if they're more than a file extension."""
    prefix = names[0]
    for n in names[1:]:
        while not n.startswith(prefix):
            prefix = prefix[:-1]
    suffix = names[0]
    for n in names[1:]:
        while not n.endswith(suffix):
            suffix = suffix[1:]
    # The suffix shouldn't cut a word in half ("*ted.vcf"); back off to a separator.
    while suffix and suffix[0].isalnum() and len(suffix) < min(len(n) for n in names) and \
            any(n[-len(suffix) - 1].isalnum() for n in names):
        suffix = suffix[1:]
    prefix = trim_partial_word(prefix, names)
    if len(prefix.strip(" ›")) < 3 and len(suffix.split(".")[0]) < 3:
        return ""
    return f"{prefix}*{suffix}"


# ── Folders with many loose files: group by name family, then by format ──────

FAMILY_AT = 20  # only reorganize folders with more loose files than this
_TOKEN_SPLIT = re.compile(r"[-_. ]+")
FORMAT_EXTENSIONS = {
    "gz", "zip", "bz2", "xz", "tgz", "tar", "csv", "tsv", "txt", "json", "jsonl", "xml", "obo", "owl", "ttl",
    "nt", "fasta", "fa", "gff", "gff3", "gtf", "xlsx", "xls", "sql", "parquet", "gmt", "rdf", "md5",
}


def dataset_stem(name: str) -> tuple[str, str]:
    """('CTD_anatomy', 'CSV') for 'CTD_anatomy.csv.gz': the name without format/compression extensions."""
    stem, exts = name, []
    while "." in stem and stem.rsplit(".", 1)[1].lower() in FORMAT_EXTENSIONS:
        stem, ext = stem.rsplit(".", 1)
        exts.append(ext.lower())
    formats = [e for e in reversed(exts) if e not in ("gz", "zip", "bz2", "xz", "tgz", "tar")] or exts
    return stem, (formats[0].upper() if formats else "")


def _merge_formats(children: list[dict]) -> list[dict]:
    """Files that are the same dataset in different formats become one entry."""
    by_stem: dict[str, list[dict]] = defaultdict(list)
    order = []
    for c in children:
        stem = dataset_stem(c["name"])[0] if c["type"] == "file" else None
        key = stem if stem is not None else id(c)
        if key not in by_stem:
            order.append(key)
        by_stem[key].append(c)
    out = []
    for key in order:
        members = by_stem[key]
        formats = {dataset_stem(m["name"])[1] for m in members}
        if isinstance(key, str) and len(members) > 1 and len(formats) > 1:
            out.append({
                "name": key, "type": "formats",
                "formats": sorted(f for f in formats if f),
                "children": members,
                "files": sum(m["files"] for m in members),
                "size": sum(m["size"] for m in members),
                "look": sum(m["look"] for m in members),
            })
        else:
            out.extend(members)
    return out


def _group_flat(children: list[dict]) -> list[dict]:
    # Loose files, plus groups of numbered files (they belong in families too).
    loose = [c for c in children if c["type"] == "file" or (c["type"] == "group" and c["of"] == "files")]
    if sum(c.get("count", 1) for c in loose) <= FAMILY_AT:
        return children
    others = [c for c in children if not any(c is l for l in loose)]
    tokens = {id(c): [t for t in _TOKEN_SPLIT.split(dataset_stem(c["name"])[0]) if t] for c in loose}
    # Skip leading words every loose file shares (e.g. "CTD"), then group by the next word.
    skip = 0
    while all(len(t) > skip + 1 for t in tokens.values()) and len({t[skip].lower() for t in tokens.values()}) == 1:
        skip += 1
    families: dict[str, list[dict]] = defaultdict(list)
    for c in loose:
        t = tokens[id(c)]
        families[t[skip] if len(t) > skip else c["name"]].append(c)
    total = sum(c.get("count", 1) for c in loose)
    if not 2 <= len(families) < total / 2:
        return others + _merge_formats(loose)  # no useful families; still merge formats
    grouped = []
    for word, members in sorted(families.items(), key=lambda kv: kv[0].lower()):
        if len(members) == 1:
            grouped.extend(_merge_formats(members))
            continue
        names = [m["name"] for m in members]
        prefix = names[0]
        for n in names[1:]:
            while not n.startswith(prefix):
                prefix = prefix[:-1]
        prefix = trim_partial_word(prefix, names)  # not "arabidopsis_prote*" for protein/proteoform
        grouped.append({
            "name": (prefix or word) + "*", "type": "family",
            "children": _merge_formats(members),
            "files": sum(m["files"] for m in members),
            "size": sum(m["size"] for m in members),
            "look": sum(m["look"] for m in members),
        })
    return others + grouped


# ── Plain names for kinds of tables ──────────────────────────────────────────

_MONTH_WORDS = {"jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec"}
_COMPRESSION_EXTS = {"gz", "zip", "bz2", "xz", "tgz", "tar"}
_FORMAT_EXTS = {"csv", "tsv", "txt", "json", "jsonl", "ndjson", "xml", "vcf", "xlsx", "xls", "tab", "parquet", "gmt",
                "chip", "obo", "owl", "gaf", "gpad", "gpi", "gff", "gff3", "gtf", "fasta", "fa", "sdf", "mol2", "pdb",
                "ttl", "nt", "db", "dat", "sql"}
# Words that say nothing about what a file holds.
_GENERIC_WORDS = {"download", "downloads", "content", "contents", "search", "files", "file", "data", "export",
                  "index", "output", "release", "current", "latest", "all", "tar", "zip"}
_HEXLIKE = re.compile(r"^[0-9a-f]{8,}$", re.IGNORECASE)


def _split_words(text: str) -> list[str]:
    # Split joined words, but not codes: FeatureSummaries -> Feature Summaries, NCBITaxon -> NCBI Taxon,
    # while hmPOS and GRCh stay whole.
    text = re.sub(r"(?<=[a-z])(?=[A-Z][a-z]{2,})", " ", text)
    text = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z]{2,})", " ", text)
    return [w for w in re.split(r"[-_.\s*%+=&]+", text) if w]


def _segment_words(segment: str) -> tuple[list[str], str]:
    """The meaningful words in one path segment, and the file format its extension names."""
    segment = segment.split("?")[0]
    fmt = ""
    while "." in segment and segment.rsplit(".", 1)[1].lower() in _FORMAT_EXTS | _COMPRESSION_EXTS:
        segment, ext = segment.rsplit(".", 1)
        if ext.lower() in _FORMAT_EXTS and not fmt:
            fmt = ext.lower()
    words: list[str] = []
    for w in _split_words(segment):
        lw = w.lower()
        if (w.isdigit() or lw in _MONTH_WORDS or len(w) < 2 or _HEXLIKE.match(w) or lw == fmt
                or re.fullmatch(r"v?\d[\d.]*", lw) or lw in (x.lower() for x in words)):
            continue
        words.append(w)
    return words, fmt


def _is_plain_word(word: str) -> bool:
    """An ordinary word ('summaries', 'Harmonized'), as opposed to a code ('hmPOS', 'GRCh', 'PGS')."""
    return len(word) >= 4 and word.isalpha() and (word.islower() or (word[0].isupper() and word[1:].islower()))


def _sentence_case(words: list[str]) -> str:
    out = []
    for i, w in enumerate(words):
        # Short all-caps words are acronyms (CTD, CADRE, NCBI); long ones are shouting (INTERACTION).
        acronym = (w.isupper() and len(w) <= 5) or (any(c.isupper() for c in w[1:]) and not w.isupper())
        if acronym:
            out.append(w)
        else:
            out.append(w.capitalize() if i == 0 else w.lower())
    return " ".join(out)


def friendly_name(pattern: str, example: str = "") -> str:
    """A plain name for a kind of table, from its file pattern: '* › *-FeatureSummaries.tsv' -> 'Feature summaries (TSV)'.

    Uses the words in the file name, adding the folder's when the file name says too little, and the format.
    Falls back to the words of an example file.
    """
    pattern = pattern.split(" (and ")[0]
    if " in " in pattern:
        # "*.csv in A › * and B": the file pattern, sitting in the first place named.
        files, places = pattern.split(" in ", 1)
        pattern = places.split(" and ")[0].replace(" › *", "") + " › " + files
    parts = [p for p in pattern.split(" › ") if p]
    words, fmt = _segment_words(parts[-1]) if parts else ([], "")
    meaningful = [w for w in words if w.lower() not in _GENERIC_WORDS and len(w) >= 3]
    folders = parts[:-1]
    # Climb into folder names while the file name says too little, or is nothing but codes (PGS*_hmPOS_GRCh*).
    while not any(_is_plain_word(w) for w in meaningful) and folders:
        folder_words, folder_fmt = _segment_words(folders.pop())
        fmt = fmt or folder_fmt
        folder_words = [w for w in folder_words if w.lower() not in _GENERIC_WORDS | _FORMAT_EXTS]
        if folder_words:
            seen = {w.lower() for w in folder_words}
            words = folder_words + [w for w in words if w.lower() not in _GENERIC_WORDS and w.lower() not in seen]
            meaningful = [w for w in words if len(w) >= 3]
    words = [w for w in words if w.lower() not in _FORMAT_EXTS]
    if not words and example:
        words, example_fmt = _segment_words(example.split(" › ")[-1])
        fmt = fmt or example_fmt
        words = [w for w in words if w.lower() not in _FORMAT_EXTS]
    if not words:
        return f"{fmt.upper()} files" if fmt else "Tables"
    title = _sentence_case(words)
    return f"{title} ({fmt.upper()})" if fmt else title
