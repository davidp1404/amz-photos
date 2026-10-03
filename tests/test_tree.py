"""Tests for task 6.1 — remote tree building."""

from __future__ import annotations

from amz_download.models import Folder, MediaType, Node
from amz_download.tree import build_remote_tree


def node(node_id: str, parents: list[str]) -> Node:
    return Node(
        node_id=node_id,
        name=f"{node_id}.jpg",
        media_type=MediaType.PHOTO,
        content_type="image/jpeg",
        parents=tuple(parents),
    )


def folder(node_id: str, name: str, parents: list[str] | None = None) -> Folder:
    return Folder(node_id=node_id, name=name, parents=tuple(parents or []))


def test_single_parent_chain():
    folders = [folder("f1", "Pictures"), folder("f2", "iPhone", ["f1"])]
    entries = build_remote_tree([node("n1", ["f2"])], folders)
    assert entries["n1"].tree_paths == ["Pictures/iPhone"]
    assert entries["n1"].primary_tree_path == "Pictures/iPhone"


def test_root_node_has_empty_path():
    entries = build_remote_tree([node("n1", [])], [])
    assert entries["n1"].tree_paths == [""]


def test_multiple_parents_primary_is_deterministic():
    folders = [folder("f1", "Alpha"), folder("f2", "Beta")]
    entries = build_remote_tree([node("n1", ["f2", "f1"])], folders)
    assert entries["n1"].tree_paths == ["Alpha", "Beta"]
    assert entries["n1"].primary_tree_path == "Alpha"


def test_missing_parent_gets_placeholder():
    entries = build_remote_tree([node("n1", ["ghost-parent-id"])], [])
    path = entries["n1"].primary_tree_path
    assert path.startswith("unknown-")
    assert path == entries["n1"].tree_paths[0]
