"""Tests for task group 4 — sync state store."""

from __future__ import annotations

import sqlite3


from amz_download import log
from amz_download.models import Album, MediaType, Node, NodeStatus
from amz_download.state import (
    SCHEMA_VERSION,
    NodeRecord,
    StateStore,
    default_state_path,
)


def make_store(tmp_path) -> StateStore:
    store = StateStore(tmp_path / ".amz-download" / "state.sqlite")
    store.initialize()
    return store


def make_node(node_id="n1", name="beach.jpg", md5="abc") -> Node:
    return Node(
        node_id=node_id,
        name=name,
        media_type=MediaType.PHOTO,
        content_type="image/jpeg",
        md5=md5,
        size=10,
    )


def make_record(node_id="n1", name="beach.jpg", md5="abc", path="2023/08/x_beach.jpg"):
    return NodeRecord.from_node(
        make_node(node_id, name, md5),
        canonical_path=path,
        tree_path="Pictures",
        tree_paths=["Pictures"],
    )


# --- 4.1 schema --------------------------------------------------------------


def test_schema_creates_expected_tables_and_columns(tmp_path):
    store = make_store(tmp_path)
    conn = sqlite3.connect(store.path)
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert {"nodes", "albums", "album_members", "runs", "failures"} <= tables
        columns = {row[1] for row in conn.execute("PRAGMA table_info(nodes)")}
        assert {
            "node_id",
            "name",
            "md5",
            "size",
            "content_date",
            "media_type",
            "tree_path",
            "canonical_path",
            "status",
            "last_seen",
            "downloaded_at",
        } <= columns
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == SCHEMA_VERSION
    finally:
        conn.close()
        store.close()


def test_initialize_is_idempotent(tmp_path):
    store = make_store(tmp_path)
    store.initialize()
    store.initialize()
    assert store.get_node("missing") is None
    store.close()


# --- 4.2 upsert and lookup ---------------------------------------------------


def test_node_upsert_update_and_readback(tmp_path):
    store = make_store(tmp_path)
    store.upsert_node(make_record())
    got = store.get_node("n1")
    assert (
        got is not None
        and got.md5 == "abc"
        and got.canonical_path == "2023/08/x_beach.jpg"
    )

    updated = make_record(md5="def", path="2023/08/y_beach.jpg")
    store.upsert_node(updated)
    got = store.get_node("n1")
    assert (
        got is not None
        and got.md5 == "def"
        and got.canonical_path == "2023/08/y_beach.jpg"
    )
    assert len(store.all_nodes()) == 1
    store.close()


def test_album_upsert_and_members(tmp_path):
    store = make_store(tmp_path)
    store.upsert_album(Album("al1", "Summer"))
    store.upsert_album(Album("al1", "Summer 2023"))
    store.replace_album_members("al1", ["n1", "n2"])
    store.replace_album_members("al1", ["n1", "n3"])
    assert store.all_albums()[0].name == "Summer 2023"
    assert sorted(store.album_members("al1")) == ["n1", "n3"]
    assert store.node_albums("n3") == ["al1"]
    store.close()


def test_run_lifecycle(tmp_path):
    store = make_store(tmp_path)
    run_id = store.start_run()
    store.finish_run(run_id, listed=5, downloaded=2, skipped=3, bytes=100)
    run = store.last_run()
    assert run is not None
    assert run.listed == 5 and run.downloaded == 2 and run.skipped == 3
    assert run.bytes == 100 and run.ended_at is not None
    store.close()


def test_failures_recorded_and_counted(tmp_path):
    store = make_store(tmp_path)
    store.record_failure("n1", "hash mismatch")
    store.record_failure("n2", "timeout")
    assert store.failure_count() == 2
    assert {f[0] for f in store.recent_failures()} == {"n1", "n2"}
    store.clear_failures()
    assert store.failure_count() == 0
    store.close()


# --- 4.3 reconciliation queries and archive transitions ----------------------


def test_absent_from_remote_lists_recorded_present_nodes(tmp_path):
    store = make_store(tmp_path)
    store.upsert_node(make_record(node_id="n1"))
    store.upsert_node(make_record(node_id="n2", name="other.jpg"))
    absent = store.absent_from_remote(remote_ids={"n2"})
    assert absent == ["n1"]
    store.close()


def test_archive_and_reappear_transitions(tmp_path):
    store = make_store(tmp_path)
    store.upsert_node(make_record(node_id="n1"))

    # Remote deletion: mark archived, do not delete the node.
    store.mark_archived("n1")
    assert store.get_node("n1").status == NodeStatus.ARCHIVED.value
    assert store.absent_from_remote(remote_ids=set()) == []  # already archived

    # Reappearance: node is back in the remote listing.
    assert store.archived_but_present(remote_ids={"n1"}) == ["n1"]
    store.clear_archived("n1")
    assert store.get_node("n1").status == NodeStatus.PRESENT.value
    assert store.archived_but_present(remote_ids={"n1"}) == []
    store.close()


def test_default_state_path(tmp_path):
    assert default_state_path(tmp_path) == tmp_path / ".amz-download" / "state.sqlite"


# --- diagnostics (verbosity) -------------------------------------------------


def test_node_upsert_logged_at_debug(debug_logging, caplog, tmp_path):
    store = make_store(tmp_path)
    store.upsert_node(make_record())
    store.close()
    assert "upserting node n1 at 2023/08/x_beach.jpg" in caplog.text


def test_node_upsert_not_logged_at_info(caplog, tmp_path):
    log.configure(1)
    store = make_store(tmp_path)
    store.upsert_node(make_record())
    store.close()
    log.configure(0)
    assert "upserting" not in caplog.text
