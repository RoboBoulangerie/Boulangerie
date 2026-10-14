"""Dotted paths into croissant JSON, e.g. ``distribution[3].encodingFormat``.

This is the same path style the pipeline's ``persistent_fields.json`` and the
review dashboard use, so provenance and findings line up with them.
"""

import re

_TOKEN = re.compile(r"([^.\[\]]+)|\[(\d+)\]")


def parse(path: str) -> list[str | int]:
    parts: list[str | int] = []
    for key, index in _TOKEN.findall(path):
        parts.append(int(index) if index else key)
    return parts


def get(doc, path: str):
    node = doc
    for part in parse(path):
        node = node[part]
    return node


def set_value(doc, path: str, value) -> None:
    *parents, last = parse(path)
    node = doc
    for part in parents:
        node = node[part]
    node[last] = value


def leaves(node, prefix: str = ""):
    """Yield (path, value) for every scalar in a JSON document."""
    if isinstance(node, dict):
        for key, child in node.items():
            yield from leaves(child, f"{prefix}.{key}" if prefix else key)
    elif isinstance(node, list):
        for i, child in enumerate(node):
            yield from leaves(child, f"{prefix}[{i}]")
    else:
        yield prefix, node
