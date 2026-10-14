"""Plain-language summaries of a croissant, for people who have never seen one."""

import re

# What each dataset-level field means, in plain words. Order is display order.
FIELD_EXPLANATIONS = [
    ("name", "Name", "What your resource is called."),
    ("description", "Description", "A short summary of what your resource contains."),
    ("url", "Homepage", "Where people can learn more."),
    ("license", "License", "The terms under which people may reuse your data."),
    ("citeAs", "How to cite", "What people should cite when they use your data."),
    ("conditionsOfAccess", "Access conditions", "Any rules people must follow to get the data."),
    ("version", "Version", "Which release this describes."),
    ("datePublished", "Release date", "When this release came out."),
    ("keywords", "Keywords", "Words that help people find your resource."),
    ("creator", "Creator", "Who made the resource."),
    ("publisher", "Publisher", "Who publishes the resource."),
]

FORMAT_NAMES = {
    "text/csv": "CSV spreadsheet",
    "text/tab-separated-values": "Tab-separated spreadsheet",
    "text/tsv": "Tab-separated spreadsheet",
    "application/json": "JSON",
    "application/jsonlines": "JSON Lines",
    "application/x-ndjson": "JSON Lines",
    "application/parquet": "Parquet table",
    "application/vnd.apache.parquet": "Parquet table",
    "application/zip": "Zip archive",
    "application/gzip": "Gzip-compressed file",
    "application/x-gzip": "Gzip-compressed file",
    "application/x-tar": "Tar archive",
    "application/pdf": "PDF document",
    "application/rdf+xml": "RDF/XML (ontology)",
    "application/owl+xml": "OWL ontology",
    "text/turtle": "Turtle (RDF)",
    "application/n-triples": "N-Triples (RDF)",
    "application/xml": "XML",
    "text/xml": "XML",
    "text/plain": "Plain text",
    "text/html": "Web page",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "Excel spreadsheet",
    "application/vnd.ms-excel": "Excel spreadsheet",
    "application/sql": "SQL database dump",
    "application/vnd.sqlite3": "SQLite database",
    "text/x-vcf": "VCF (variants)",
    "text/obo": "OBO ontology",
}

_SIZE_RE = re.compile(r"^\s*([\d.,]+)\s*([KMGT]?i?B|bytes?)?\s*$", re.IGNORECASE)
_UNITS = {"b": 1, "byte": 1, "bytes": 1, "kb": 1e3, "mb": 1e6, "gb": 1e9, "tb": 1e12,
          "kib": 1024, "mib": 1024**2, "gib": 1024**3, "tib": 1024**4}


def parse_size(value) -> int | None:
    if isinstance(value, (int, float)):
        return int(value)
    if not isinstance(value, str):
        return None
    m = _SIZE_RE.match(value)
    if not m:
        return None
    number = float(m.group(1).replace(",", ""))
    unit = (m.group(2) or "b").lower()
    return int(number * _UNITS.get(unit, 1))


def human_size(n: int | None) -> str:
    if n is None:
        return "unknown size"
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if n < 1000 or unit == "TB":
            return f"{n:,.0f} {unit}" if unit == "bytes" else f"{n:,.1f} {unit}"
        n /= 1000
    return ""


def format_name(encoding_format) -> str:
    if isinstance(encoding_format, list):
        encoding_format = encoding_format[0] if encoding_format else None
    if not encoding_format:
        return "Unknown type"
    return FORMAT_NAMES.get(encoding_format.lower(), encoding_format)


def display_value(value) -> str:
    """Render a JSON value as readable text."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return ", ".join(display_value(v) for v in value)
    if isinstance(value, dict):
        for key in ("name", "url", "@id"):
            if key in value:
                return display_value(value[key])
        return ", ".join(f"{k}: {display_value(v)}" for k, v in value.items() if not k.startswith("@"))
    return str(value)


def summarize(croissant: dict, max_examples: int = 8) -> dict:
    """Numbers and examples for the 'your files' and 'your tables' sections."""
    dists = croissant.get("distribution") or []
    record_sets = croissant.get("recordSet") or []

    by_format: dict[str, dict] = {}
    total_size, sized = 0, 0
    for d in dists:
        name = format_name(d.get("encodingFormat"))
        group = by_format.setdefault(name, {"format": name, "count": 0, "size": 0, "examples": []})
        group["count"] += 1
        size = parse_size(d.get("contentSize"))
        if size is not None:
            group["size"] += size
            total_size += size
            sized += 1
        if len(group["examples"]) < max_examples:
            group["examples"].append({
                "name": d.get("name") or d.get("@id"),
                "url": d.get("contentUrl"),
                "size": human_size(size) if size is not None else "",
                "inside": bool(d.get("containedIn")),
                "is_set": d.get("@type") == "cr:FileSet",
                "pattern": d.get("includes"),
            })
    formats = sorted(by_format.values(), key=lambda g: -g["count"])
    for g in formats:
        g["size_display"] = human_size(g["size"]) if g["size"] else ""

    tables = []
    field_count = 0
    for rs in record_sets:
        fields = rs.get("field") or []
        field_count += len(fields)
        if len(tables) < max_examples:
            tables.append({
                "name": rs.get("name") or rs.get("@id"),
                "description": rs.get("description", ""),
                "columns": [f.get("name") for f in fields[:25]],
                "more_columns": max(0, len(fields) - 25),
            })

    return {
        "file_count": len(dists),
        "total_size": human_size(total_size) if sized else "",
        "sized_count": sized,
        "formats": formats,
        "table_count": len(record_sets),
        "column_count": field_count,
        "tables": tables,
    }
