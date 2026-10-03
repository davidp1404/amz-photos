"""Canonical path derivation (design D5).

Canonical paths depend only on a node's content date and original name, so
Amazon-side reorganization never moves a file. Undated media goes to a
deterministic ``_unsorted/`` fallback. Collisions are broken deterministically
by appending a short node id.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Iterable

from .models import Node

UNSORTED_DIR = "_unsorted"
_UNSAFE = re.compile(r"[\x00-\x1f/\\]")
_EXT_BY_TYPE = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/heic": "heic",
    "image/heif": "heif",
    "image/tiff": "tiff",
    "image/bmp": "bmp",
    "video/mp4": "mp4",
    "video/quicktime": "mov",
    "video/x-msvideo": "avi",
    "video/x-matroska": "mkv",
    "video/webm": "webm",
    "video/mpeg": "mpeg",
}


def sanitize_component(value: str, fallback: str = "_") -> str:
    """Make a single path component filesystem-safe and non-empty."""
    cleaned = _UNSAFE.sub("_", value).strip().strip(".")
    return cleaned or fallback


def sanitize_tree_path(value: str) -> str:
    """Sanitize a ``/``-separated tree path, preserving separators."""
    parts = [sanitize_component(part) for part in value.split("/") if part]
    return "/".join(parts)


def short_id(node_id: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]", "", node_id) or "0"
    return cleaned[-8:]


def extension_from_content_type(content_type: str | None) -> str:
    if content_type and content_type.lower() in _EXT_BY_TYPE:
        return _EXT_BY_TYPE[content_type.lower()]
    return "bin"


def _filename(node: Node) -> str:
    name = sanitize_component(node.name or node.node_id, fallback=node.node_id)
    if not PurePosixPath(name).suffix:
        name = f"{name}.{extension_from_content_type(node.content_type)}"
    return name


def _date_part(content_date: datetime) -> str:
    if content_date.tzinfo is not None:
        content_date = content_date.astimezone(timezone.utc)
    return content_date.strftime("%Y-%m-%d")


def base_relative_path(node: Node) -> PurePosixPath:
    """The collision-free-except-for-duplicates canonical path for a node."""
    filename = _filename(node)
    if node.content_date is None:
        return PurePosixPath(UNSORTED_DIR) / filename
    date = _date_part(node.content_date)
    return PurePosixPath(f"{date[:4]}") / date[5:7] / f"{date}_{filename}"


def with_collision_suffix(path: PurePosixPath, node_id: str) -> PurePosixPath:
    stem = path.stem
    return path.parent / f"{stem}_{short_id(node_id)}{path.suffix}"


def resolve_canonical_paths(nodes: Iterable[Node]) -> dict[str, PurePosixPath]:
    """Resolve all nodes to distinct, deterministic relative paths.

    Nodes are grouped by base path; within a group the lexicographically
    smallest ``node_id`` keeps the plain path and the rest receive a short-id
    suffix, so the assignment is stable across runs.
    """
    groups: dict[PurePosixPath, list[Node]] = {}
    for node in nodes:
        groups.setdefault(base_relative_path(node), []).append(node)

    result: dict[str, PurePosixPath] = {}
    for base, group in groups.items():
        ordered = sorted(group, key=lambda n: n.node_id)
        if len(ordered) == 1:
            result[ordered[0].node_id] = base
            continue
        for index, node in enumerate(ordered):
            result[node.node_id] = (
                base if index == 0 else with_collision_suffix(base, node.node_id)
            )
    return result
