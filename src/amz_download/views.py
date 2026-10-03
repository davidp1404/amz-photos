"""Regenerable symlink views over the canonical store (design D5).

Views are derived entirely from recorded state and can be rebuilt at any time.
They are implemented as symbolic links so no media bytes are duplicated, and
symlink failure degrades gracefully without touching canonical media.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Callable

from .models import NodeStatus
from .paths import sanitize_component, sanitize_tree_path
from .state import StateStore

BY_TREE_DIR = "_by-tree"
BY_ALBUM_DIR = "_by-album"


@dataclass(slots=True)
class ViewResult:
    links: int = 0
    created: int = 0
    updated: int = 0
    removed: int = 0
    skipped: bool = False
    warnings: list[str] = field(default_factory=list)


def _present_records(store: StateStore):
    return {
        record.node_id: record for record in store.nodes_by_status(NodeStatus.PRESENT)
    }


def _basename(canonical_path: str) -> str:
    return PurePosixPath(canonical_path).name


def desired_links(store: StateStore, dest: str | os.PathLike[str]) -> dict[Path, Path]:
    """Map each desired symlink path to its canonical target path."""
    dest = Path(dest)
    links: dict[Path, Path] = {}
    records = _present_records(store)

    for record in records.values():
        target = dest / Path(record.canonical_path)
        basename = _basename(record.canonical_path)
        for tree_path in record.tree_paths or [record.tree_path]:
            view_dir = Path(BY_TREE_DIR)
            sanitized = sanitize_tree_path(tree_path)
            if sanitized:
                view_dir = view_dir / Path(sanitized)
            links[dest / view_dir / basename] = target

    album_names = {album.album_id: album.name for album in store.all_albums()}
    for album_id, node_ids in store.all_album_members().items():
        album_dir = Path(BY_ALBUM_DIR) / sanitize_component(
            album_names.get(album_id, album_id)
        )
        for node_id in node_ids:
            record = records.get(node_id)
            if record is None:
                continue
            links[dest / album_dir / _basename(record.canonical_path)] = dest / Path(
                record.canonical_path
            )
    return links


def generate_views(
    store: StateStore,
    dest: str | os.PathLike[str],
    *,
    warn: Callable[[str], None] | None = None,
) -> ViewResult:
    """Create/update desired links, prune stale ones, leave canonical files alone."""
    dest = Path(dest)
    result = ViewResult()
    emit = warn or (lambda message: None)
    desired = desired_links(store, dest)

    for link, target in sorted(desired.items(), key=lambda item: str(item[0])):
        try:
            link.parent.mkdir(parents=True, exist_ok=True)
            relative_target = os.path.relpath(target, link.parent)
            if link.is_symlink():
                if os.readlink(link) == relative_target:
                    result.links += 1
                    continue
                link.unlink()
                os.symlink(relative_target, link)
                result.updated += 1
            elif link.exists():
                message = f"refusing to replace non-symlink at {link}"
                result.warnings.append(message)
                emit(message)
                continue
            else:
                os.symlink(relative_target, link)
                result.created += 1
            result.links += 1
        except OSError as exc:
            result.skipped = True
            message = f"views skipped: symbolic links are unavailable ({exc})"
            result.warnings.append(message)
            emit(message)
            return result

    _prune(dest / BY_TREE_DIR, desired, result)
    _prune(dest / BY_ALBUM_DIR, desired, result)
    return result


def _prune(view_root: Path, desired: dict[Path, Path], result: ViewResult) -> None:
    if not view_root.exists():
        return
    for path in sorted(view_root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if path.is_symlink() and path not in desired:
            try:
                path.unlink()
                result.removed += 1
            except OSError:
                continue
    for path in sorted(
        (p for p in view_root.rglob("*") if p.is_dir()),
        key=lambda p: len(p.parts),
        reverse=True,
    ):
        try:
            path.rmdir()
        except OSError:
            continue
