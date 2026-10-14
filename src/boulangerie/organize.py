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

import re
from collections import Counter, defaultdict
from urllib.parse import unquote, urlsplit

from boulangerie.filesets import _schema_signature
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


def _sort_key(node: dict):
    # Folders before files, then natural order (file_2 before file_10).
    leaf = node["type"] in ("file", "fileset")
    return (leaf, [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", node["name"])])


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
    signatures = [_schema_signature(rs) for rs in record_sets]
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
            family_layouts.append({
                "tables": tables,
                "files": _natural_sorted({file_paths.get(s, s) for i in tables for s in sources[i]}),
                "columns": _columns(record_sets[tables[0]].get("field") or []),
                "look": sum(look[i] for i in tables),
            })
        column_sets = [{c[0] for c in layout["columns"]} for layout in family_layouts]
        common_names = set.intersection(*column_sets)
        common = [c for c in family_layouts[0]["columns"] if c[0] in common_names]
        for layout in family_layouts:
            layout["extra"] = [c for c in layout["columns"] if c[0] not in common_names]
        family_layouts.sort(key=lambda l: (-l["look"], -len(l["tables"])))
        files = _natural_sorted({f for l in family_layouts for f in l["files"]})
        out.append({
            "name": _family_name(record_sets, members, files),
            "common": common,
            "layouts": family_layouts,
            "files": len(files),
            "tables": len(members),
            "look": sum(l["look"] for l in family_layouts),
        })
    return sorted(_merge_same_name(out), key=lambda f: (f["look"] == 0, -f["tables"], f["name"].lower()))


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
            "common": [c for c in layouts[0]["columns"] if c[0] in common_names],
            "layouts": layouts,
            "files": sum(f["files"] for f in group),
            "tables": sum(f["tables"] for f in group),
            "look": sum(f["look"] for f in group),
        })
    return merged


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
    """Name a family after its files: their shared path pattern, else what their paths share, else an example."""
    if len(files) == 1:
        return files[0]
    if not files:
        rs = record_sets[members[0]]
        return re.sub(r"^Records from ", "", rs.get("name") or rs.get("@id") or "Table")
    patterns = Counter(name_pattern(f) for f in files)
    top, n = patterns.most_common(1)[0]
    if n == len(files):
        return top
    if n >= len(files) / 2:
        return f"{top} (+{len(files) - n:,} other files)"
    return _wordwise_pattern(list(patterns)) or _common_affixes(list(patterns)) or \
        f"{files[0]} and {len(files) - 1:,} similar"


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
    pattern = re.sub(r"\*(?:[-_.]\*)+", "*", "".join(merged))
    others = len(names) - len(split)
    return f"{pattern} (+{others:,} other kinds)" if others else pattern


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
        # Don't stop mid-word ("arabidopsis_prote*" for protein/proteoform): back off to a separator.
        if prefix and prefix[-1].isalnum() and any(len(n) > len(prefix) and n[len(prefix)].isalnum() for n in names):
            prefix = re.sub(r"[^-_. ]+$", "", prefix)
        grouped.append({
            "name": (prefix or word) + "*", "type": "family",
            "children": _merge_formats(members),
            "files": sum(m["files"] for m in members),
            "size": sum(m["size"] for m in members),
            "look": sum(m["look"] for m in members),
        })
    return others + grouped
