"""Collapse repeated archive members into Croissant FileSets.

Pipelines often list every member of an archive as its own FileObject, each with
its own RecordSet, even when hundreds of members share a name pattern and
columns (FlyBase lists 84,252 ``scaffold_N.raw`` files this way). Croissant's
``cr:FileSet`` describes them in one entry: a glob inside the archive.

A group of members becomes a FileSet when all of these hold:

- the members are in the same archive and have the same ``encodingFormat``
- their names match the same pattern once digit runs are replaced by ``*``
- their RecordSets (if any) have the same fields, and each RecordSet reads from
  exactly one member
- the glob matches exactly those members among everything we know is in the archive

Each group keeps one RecordSet, which now reads from the FileSet. Anything that
doesn't meet these rules is left alone. Remote files that aren't in an archive
are never grouped: Croissant 1.1 can only glob inside a container.
"""

import copy
import fnmatch
import re
from collections import defaultdict
from dataclasses import dataclass

from boulangerie.summary import human_size, parse_size

# Small families of similar files (MSigDB's C1-C8 collections) are distinct products to their owners;
# the repetition worth collapsing is in the hundreds or thousands.
DEFAULT_MIN_GROUP = 20
_DIGITS = re.compile(r"\d+")


@dataclass
class GroupReport:
    archive: str
    pattern: str
    files: int
    record_sets_removed: int


def _ref_id(value) -> str | None:
    return value.get("@id") if isinstance(value, dict) else None


def _source_file_ids(record_set: dict) -> set[str]:
    ids = set()
    for field in _walk(record_set.get("field") or []):
        source = field.get("source") or {}
        ref = _ref_id(source.get("fileObject")) or _ref_id(source.get("fileSet"))
        if ref:
            ids.add(ref)
    return ids


def _walk(fields: list[dict]):
    for f in fields:
        yield f
        yield from _walk(f.get("subField") or [])


def _schema_signature(record_set: dict) -> tuple:
    """Fields and how they're read, ignoring which file and the generated ids/descriptions."""
    def sig(f):
        source = f.get("source") or {}
        return (
            f.get("name"),
            str(f.get("dataType")),
            repr(source.get("extract")),
            repr(source.get("transform")),
            tuple(sig(s) for s in f.get("subField") or []),
        )
    return tuple(sig(f) for f in record_set.get("field") or [])


def _point_at(fields: list[dict], fileset_id: str) -> None:
    for f in _walk(fields):
        source = f.get("source")
        if source and ("fileObject" in source or "fileSet" in source):
            source.pop("fileObject", None)
            source["fileSet"] = {"@id": fileset_id}


def _references(node, out: set[str]) -> None:
    """Collect every {"@id": x} reference (a dict whose only key is @id)."""
    if isinstance(node, dict):
        if set(node) == {"@id"}:
            out.add(node["@id"])
        for value in node.values():
            _references(value, out)
    elif isinstance(node, list):
        for value in node:
            _references(value, out)


def _defined_ids(croissant: dict) -> set[str]:
    ids = set()

    def walk(node):
        if isinstance(node, dict):
            if "@id" in node and len(node) > 1:
                ids.add(node["@id"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(croissant.get("distribution"))
    walk(croissant.get("recordSet"))
    return ids


def _dangling(croissant: dict) -> set[str]:
    refs: set[str] = set()
    _references(croissant.get("distribution"), refs)
    _references(croissant.get("recordSet"), refs)
    return refs - _defined_ids(croissant)


def collapse_filesets(croissant: dict, min_group: int = DEFAULT_MIN_GROUP) -> tuple[dict, list[GroupReport]]:
    """Return a new croissant with repeated archive members collapsed, and what was collapsed."""
    result = copy.deepcopy(croissant)
    dists = result.get("distribution") or []
    record_sets = result.get("recordSet") or []

    # Which record sets read from exactly one file, by that file's id.
    rs_by_file: dict[str, list[int]] = defaultdict(list)
    multi_source_files: set[str] = set()
    for i, rs in enumerate(record_sets):
        sources = _source_file_ids(rs)
        if len(sources) == 1:
            rs_by_file[next(iter(sources))].append(i)
        else:
            multi_source_files |= sources

    # Group archive members by archive, format, name pattern and record set schema.
    members_by_archive: dict[str, list[int]] = defaultdict(list)
    groups: dict[tuple, list[int]] = defaultdict(list)
    for i, d in enumerate(dists):
        archive = _ref_id(d.get("containedIn"))
        url = d.get("contentUrl")
        if d.get("@type") != "cr:FileObject" or not archive or not isinstance(url, str):
            continue
        members_by_archive[archive].append(i)
        if d.get("@id") in multi_source_files or len(rs_by_file.get(d.get("@id"), [])) > 1:
            continue  # its tables combine several files, or it feeds several tables; leave it alone
        rs_index = rs_by_file.get(d.get("@id"), [None])[0]
        schema = _schema_signature(record_sets[rs_index]) if rs_index is not None else None
        pattern = _DIGITS.sub("*", url)
        if pattern == url:
            continue  # no varying part to glob over
        groups[(archive, str(d.get("encodingFormat")), pattern, schema)].append(i)

    remove_dists: set[int] = set()
    remove_rs: set[int] = set()
    new_filesets: list[dict] = []
    inserted_at: dict[int, list[dict]] = {}  # each FileSet goes where its first member was
    reports: list[GroupReport] = []
    used_ids = {d.get("@id") for d in dists}

    for (archive, _fmt, pattern, schema), members in sorted(groups.items(), key=lambda kv: kv[1][0]):
        if len(members) < min_group:
            continue
        # The glob must match exactly this group among everything known to be in the archive.
        in_archive = {dists[i]["contentUrl"] for i in members_by_archive[archive]}
        matched = {u for u in in_archive if fnmatch.fnmatchcase(u, pattern)}
        if matched != {dists[i]["contentUrl"] for i in members}:
            continue

        first = dists[members[0]]
        fileset_id = f"{archive}/fileset-{len(new_filesets)}"
        while fileset_id in used_ids:
            fileset_id += "-"
        used_ids.add(fileset_id)
        sizes = [parse_size(dists[i].get("contentSize")) for i in members]
        total = sum(s for s in sizes if s is not None)
        # Some pipelines write members as "archive.zip!/path"; a glob is evaluated from the archive's root.
        includes = pattern.split("!/", 1)[-1]
        fileset = {
            "@id": fileset_id,
            "@type": "cr:FileSet",
            "name": includes,
            "description": f"{len(members):,} files matching {includes}"
                           + (f", {human_size(total)} in total" if total else "") + ".",
            "containedIn": {"@id": archive},
            "encodingFormat": first.get("encodingFormat"),
            "includes": includes,
        }
        new_filesets.append(fileset)
        inserted_at.setdefault(members[0], []).append(fileset)
        remove_dists.update(members)

        rs_indexes = [rs_by_file[dists[i]["@id"]][0] for i in members if dists[i]["@id"] in rs_by_file]
        if schema is not None and rs_indexes:
            keep = record_sets[rs_indexes[0]]
            _point_at(keep.get("field") or [], fileset_id)
            keep["name"] = f"Records from {includes}"
            keep["description"] = f"Records from the {len(members):,} files matching {includes}."
            remove_rs.update(rs_indexes[1:])
        reports.append(GroupReport(archive, includes, len(members), max(0, len(rs_indexes) - 1)))

    if not reports:
        return result, []

    new_dists = []
    for i, d in enumerate(dists):
        new_dists.extend(inserted_at.get(i, []))
        if i not in remove_dists:
            new_dists.append(d)
    result["distribution"] = new_dists
    result["recordSet"] = [rs for i, rs in enumerate(record_sets) if i not in remove_rs]

    # Fail loudly rather than publish a croissant with references to things we removed.
    # References that were already broken are the checks' job to report, not ours.
    dangling = _dangling(result) - _dangling(croissant)
    if dangling:
        raise ValueError(f"Collapsing file sets left {len(dangling)} dangling references, e.g. {sorted(dangling)[:3]}")
    return result, reports
