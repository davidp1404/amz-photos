"""Build a deterministic local view of Amazon's folder tree from listed nodes.

Amazon's tree is mutable and a node may have zero, one, or many parents. This
module resolves every media node to one or more folder paths and chooses a
deterministic primary path (design D5, risks: multi/missing parents).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .models import Folder, Node
from .paths import short_id


@dataclass(slots=True)
class RemoteEntry:
    node: Node
    tree_paths: list[str]
    primary_tree_path: str


def build_remote_tree(
    media_nodes: Iterable[Node], folders: Iterable[Folder]
) -> dict[str, RemoteEntry]:
    folder_map: dict[str, Folder] = {folder.node_id: folder for folder in folders}
    memo: dict[str, str] = {}

    def resolve(folder_id: str) -> str:
        if folder_id in memo:
            return memo[folder_id]
        folder = folder_map.get(folder_id)
        if folder is None:
            # A parent that was not listed: keep a deterministic placeholder.
            return f"unknown-{short_id(folder_id)}"
        parent_id = folder.parents[0] if folder.parents else None
        if not parent_id or parent_id == folder_id or parent_id not in folder_map:
            path = folder.name
        else:
            parent_path = resolve(parent_id)
            path = f"{parent_path}/{folder.name}" if parent_path else folder.name
        memo[folder_id] = path
        return path

    entries: dict[str, RemoteEntry] = {}
    for node in media_nodes:
        memberships: set[str] = set()
        for parent in node.parents:
            resolved = resolve(parent)
            if resolved:
                memberships.add(resolved)
        ordered = sorted(memberships)
        if not ordered:
            ordered = [""]
        entries[node.node_id] = RemoteEntry(
            node=node,
            tree_paths=ordered,
            primary_tree_path=ordered[0],
        )
    return entries
