"""Tests for tasks 5.3-5.5 — symlink views."""

from __future__ import annotations

import os
from pathlib import Path

from amz_download.models import Album, MediaType, Node, NodeStatus
from amz_download.state import NodeRecord, StateStore
from amz_download.views import BY_ALBUM_DIR, BY_TREE_DIR, generate_views


def make_store(tmp_path) -> StateStore:
    store = StateStore(tmp_path / ".amz-download" / "state.sqlite")
    store.initialize()
    return store


def add_media(
    store,
    dest,
    node_id="n1",
    *,
    canonical="2023/08/2023-08-14_beach.jpg",
    tree_paths=None,
    status=NodeStatus.PRESENT,
    data=b"canonical-bytes",
):
    node = Node(
        node_id=node_id,
        name="beach.jpg",
        media_type=MediaType.PHOTO,
        content_type="image/jpeg",
        md5="abc",
    )
    record = NodeRecord.from_node(
        node,
        canonical_path=canonical,
        tree_path="Pictures",
        tree_paths=tree_paths if tree_paths is not None else ["Pictures"],
        status=status,
    )
    store.upsert_node(record)
    target = Path(dest) / canonical
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


def test_views_reference_canonical_files(tmp_path):
    dest = tmp_path / "lib"
    store = make_store(tmp_path)
    target = add_media(store, dest, tree_paths=["Pictures/iPhone"])
    store.upsert_album(Album("al1", "Summer"))
    store.replace_album_members("al1", ["n1"])

    result = generate_views(store, dest)
    assert result.skipped is False

    tree_link = dest / BY_TREE_DIR / "Pictures" / "iPhone" / "2023-08-14_beach.jpg"
    album_link = dest / BY_ALBUM_DIR / "Summer" / "2023-08-14_beach.jpg"
    assert tree_link.is_symlink()
    assert album_link.is_symlink()
    assert tree_link.resolve() == target.resolve()
    assert album_link.resolve() == target.resolve()
    assert tree_link.read_bytes() == b"canonical-bytes"
    store.close()


def test_regeneration_prunes_stale_and_adds_new(tmp_path):
    dest = tmp_path / "lib"
    store = make_store(tmp_path)
    target = add_media(store, dest, tree_paths=["Pictures"])
    generate_views(store, dest)
    old_link = dest / BY_TREE_DIR / "Pictures" / "2023-08-14_beach.jpg"
    assert old_link.is_symlink()
    original_bytes = target.read_bytes()

    # Node moves to a new tree location.
    record = store.get_node("n1")
    store.update_location(
        "n1",
        canonical_path=record.canonical_path,
        tree_path="Camera",
        tree_paths=["Camera"],
    )
    generate_views(store, dest)

    new_link = dest / BY_TREE_DIR / "Camera" / "2023-08-14_beach.jpg"
    assert not old_link.exists()
    assert new_link.is_symlink()
    assert new_link.resolve() == target.resolve()
    assert target.read_bytes() == original_bytes
    store.close()


def test_archived_nodes_are_not_linked(tmp_path):
    dest = tmp_path / "lib"
    store = make_store(tmp_path)
    add_media(store, dest, status=NodeStatus.ARCHIVED)
    generate_views(store, dest)
    assert not (dest / BY_TREE_DIR).exists() or not list(
        (dest / BY_TREE_DIR).rglob("*.jpg")
    )
    store.close()


def test_symlink_failure_degrades_gracefully(tmp_path, monkeypatch):
    dest = tmp_path / "lib"
    store = make_store(tmp_path)
    target = add_media(store, dest)

    def boom(*args, **kwargs):
        raise OSError("operation not supported")

    monkeypatch.setattr(os, "symlink", boom)
    result = generate_views(store, dest)
    assert result.skipped is True
    assert result.warnings and "unavailable" in result.warnings[0]
    # Canonical media is untouched.
    assert target.read_bytes() == b"canonical-bytes"
    store.close()
