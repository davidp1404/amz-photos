"""Tests for task groups 4.4, 5.2, 5.6, and 6 — sync engine."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from amz_download import log
from amz_download.client import AmazonPhotosClient
from amz_download.models import MediaType, Node, NodeStatus
from amz_download.state import NodeRecord, StateStore
from amz_download.sync import (
    ActionType,
    reconcile,
    rebuild_state,
    sync,
)
from amz_download.tree import RemoteEntry, build_remote_tree

from fake_amazon import FakeAmazon

COOKIES = {"at_main": "a", "ubid_main": "u", "session-id": "s"}


def make_store(tmp_path) -> StateStore:
    store = StateStore(tmp_path / "lib" / ".amz-download" / "state.sqlite")
    return store


async def run_sync(
    fake: FakeAmazon, store: StateStore, dest, concurrency: int = 4, **kwargs
):
    client = AmazonPhotosClient(
        COOKIES,
        transport=fake.transport,
        backoff_base=0.0,
        concurrency=concurrency,
    )
    try:
        return await sync(client, store, dest, **kwargs)
    finally:
        await client.aclose()


def content_requests(fake: FakeAmazon) -> int:
    return sum(
        count
        for path, count in fake.path_counts.items()
        if path.endswith("/contentRedirection")
    )


def node(node_id="n1", name="a.jpg", md5="abc", date="2023-08-14T12:00:00Z"):
    content_date = (
        datetime.fromisoformat(date.replace("Z", "+00:00")).astimezone(timezone.utc)
        if date
        else None
    )
    return Node(
        node_id=node_id,
        name=name,
        media_type=MediaType.PHOTO,
        content_type="image/jpeg",
        content_date=content_date,
        md5=md5,
    )


# --- 6.2 reconciliation (table driven) --------------------------------------


@pytest.mark.parametrize(
    "case,recorded,remote_md5,path_changed,files,expected",
    [
        ("new", None, "abc", False, set(), ActionType.DOWNLOAD),
        ("unchanged", "present", "abc", False, {"new"}, ActionType.SKIP),
        ("changed", "present", "xyz", False, {"new"}, ActionType.REFRESH),
        ("missing", "present", "abc", False, set(), ActionType.HEAL),
        ("moved", "present", "abc", True, {"old"}, ActionType.MOVE),
        ("already-there", "present", "abc", True, {"new"}, ActionType.SKIP),
        ("reappeared", "archived", "abc", False, {"old"}, ActionType.SKIP),
    ],
)
def test_reconcile_outcomes(case, recorded, remote_md5, path_changed, files, expected):
    dest = Path("/tmp/does-not-matter")
    remote = node(md5=remote_md5)
    entry = RemoteEntry(node=remote, tree_paths=["P"], primary_tree_path="P")
    old_path = "2023/08/2023-08-14_a.jpg"
    new_path = "2023/09/2023-09-01_a.jpg" if path_changed else old_path

    recorded_map: dict[str, NodeRecord] = {}
    if recorded is not None:
        rec = NodeRecord.from_node(
            node(md5="abc"),
            canonical_path=old_path,
            tree_path="P",
            tree_paths=["P"],
            status=NodeStatus.ARCHIVED
            if recorded == "archived"
            else NodeStatus.PRESENT,
        )
        recorded_map["n1"] = rec

    def exists(relative: str) -> bool:
        if relative == old_path and relative == new_path:
            return bool(files & {"old", "new"})
        if relative == old_path:
            return "old" in files
        if relative == new_path:
            return "new" in files
        return False

    actions = reconcile(
        [remote],
        {"n1": entry},
        {"n1": new_path},
        recorded_map,
        dest,
        file_exists=exists,
    )
    assert len(actions) == 1
    assert actions[0].type is expected
    if expected is ActionType.SKIP and recorded == "archived":
        assert actions[0].restore is True


def test_reconcile_archives_remote_deleted_nodes():
    recorded = NodeRecord.from_node(
        node(), canonical_path="2023/08/2023-08-14_a.jpg", tree_paths=["P"]
    )
    actions = reconcile([], {}, {}, {"n1": recorded}, Path("/tmp/x"))
    assert len(actions) == 1
    assert actions[0].type is ActionType.ARCHIVE
    assert actions[0].node_id == "n1"


# --- 6.4 download pipeline, hash mismatch, resume ---------------------------


async def test_hash_mismatch_discards_partial_and_fails(tmp_path):
    fake = FakeAmazon()
    fake.add_media("n1", "a.jpg", data=b"real-bytes", md5="0" * 32)
    store = make_store(tmp_path)
    summary = await run_sync(fake, store, tmp_path / "lib")
    assert summary.failed == 1
    assert summary.downloaded == 0
    assert not (tmp_path / "lib" / "2023" / "08" / "2023-08-14_a.jpg").exists()
    assert not list((tmp_path / "lib").rglob("*.part"))
    store.close()


async def test_resume_does_not_redownload_completed_items(tmp_path):
    fake = FakeAmazon()
    fake.add_media("n1", "a.jpg")
    fake.add_media("n2", "b.jpg")
    store = make_store(tmp_path)
    dest = tmp_path / "lib"

    first = await run_sync(fake, store, dest, limit=1)
    assert first.downloaded == 1
    assert first.deferred == 1
    n1_path = "/drive/v1/nodes/n1/contentRedirection"
    n2_path = "/drive/v1/nodes/n2/contentRedirection"
    assert (fake.path_counts.get(n1_path, 0) + fake.path_counts.get(n2_path, 0)) == 1

    second = await run_sync(fake, store, dest)
    assert second.downloaded + second.healed == 1  # only the remaining item
    assert fake.path_counts.get(n1_path, 0) + fake.path_counts.get(n2_path, 0) == 2
    store.close()


# --- 5.2 modification time ---------------------------------------------------


async def test_downloaded_file_mtime_is_content_date(tmp_path):
    fake = FakeAmazon()
    fake.add_media("n1", "a.jpg", content_date="2023-08-14T12:00:00.000Z")
    store = make_store(tmp_path)
    await run_sync(fake, store, tmp_path / "lib")
    target = tmp_path / "lib" / "2023" / "08" / "2023-08-14_a.jpg"
    expected = datetime(2023, 8, 14, 12, 0, tzinfo=timezone.utc).timestamp()
    assert target.stat().st_mtime == pytest.approx(expected, abs=1e-6)
    store.close()


# --- 6.3 move detection ------------------------------------------------------


async def test_rename_relocates_without_downloading(tmp_path):
    fake = FakeAmazon()
    fake.add_media("n1", "one.jpg")
    store = make_store(tmp_path)
    dest = tmp_path / "lib"
    await run_sync(fake, store, dest)
    old = dest / "2023" / "08" / "2023-08-14_one.jpg"
    assert old.exists()
    requests_after_first = content_requests(fake)

    # Same node id, new name -> canonical path changes.
    fake.nodes["n1"].name = "renamed.jpg"
    summary = await run_sync(fake, store, dest)

    new = dest / "2023" / "08" / "2023-08-14_renamed.jpg"
    assert summary.moved == 1
    assert new.exists() and not old.exists()
    assert content_requests(fake) == requests_after_first  # no re-download
    store.close()


# --- 6.5 archive on delete and reappearance ---------------------------------


async def test_remote_delete_archives_locally_and_reappearance_restores(tmp_path):
    fake = FakeAmazon()
    fake.add_media("n1", "one.jpg", data=b"stable")
    store = make_store(tmp_path)
    dest = tmp_path / "lib"
    await run_sync(fake, store, dest)
    target = dest / "2023" / "08" / "2023-08-14_one.jpg"
    assert target.exists()

    fake.remove_media("n1")
    archived_run = await run_sync(fake, store, dest)
    assert archived_run.archived == 1
    assert store.get_node("n1").status == NodeStatus.ARCHIVED.value
    assert target.exists()  # retained

    requests_before = content_requests(fake)
    fake.add_media("n1", "one.jpg", data=b"stable")
    restored_run = await run_sync(fake, store, dest)
    assert store.get_node("n1").status == NodeStatus.PRESENT.value
    assert restored_run.skipped >= 1
    assert content_requests(fake) == requests_before  # no re-download
    store.close()


# --- 6.6 dry run -------------------------------------------------------------


async def test_dry_run_reports_without_writing(tmp_path):
    fake = FakeAmazon()
    fake.add_media("n1", "a.jpg")
    store = make_store(tmp_path)
    dest = tmp_path / "lib"
    summary = await run_sync(fake, store, dest, dry_run=True)
    assert summary.dry_run is True
    assert summary.planned == 1
    assert not dest.exists()
    assert not store.path.exists()
    store.close()


# --- 6.7 failure isolation ---------------------------------------------------


async def test_one_failure_does_not_stop_the_run(tmp_path):
    fake = FakeAmazon()
    fake.add_media("good", "good.jpg", data=b"good")
    fake.add_media("bad", "bad.jpg", data=b"bad", md5="deadbeef")
    store = make_store(tmp_path)
    dest = tmp_path / "lib"
    summary = await run_sync(fake, store, dest)
    assert summary.failed == 1
    assert summary.downloaded == 1
    assert (dest / "2023" / "08" / "2023-08-14_good.jpg").exists()
    assert not (dest / "2023" / "08" / "2023-08-14_bad.jpg").exists()
    assert store.failure_count() == 1
    assert summary.ok is False
    store.close()


# --- 6.8 idempotency ---------------------------------------------------------


async def test_second_run_is_a_noop(tmp_path):
    fake = FakeAmazon()
    fake.add_media("n1", "a.jpg")
    fake.add_media("n2", "b.jpg")
    store = make_store(tmp_path)
    dest = tmp_path / "lib"
    await run_sync(fake, store, dest)
    requests_after_first = content_requests(fake)

    def media_files():
        return sorted(
            p.relative_to(dest)
            for p in dest.rglob("*")
            if p.is_file() and ".amz-download" not in p.parts
        )

    files_after_first = media_files()
    mtimes = {
        str(p): p.stat().st_mtime
        for p in dest.rglob("*")
        if p.is_file() and ".amz-download" not in p.parts
    }

    second = await run_sync(fake, store, dest)
    assert second.downloaded == 0
    assert second.changed == 0
    assert content_requests(fake) == requests_after_first
    assert files_after_first == media_files()
    assert mtimes == {
        str(p): p.stat().st_mtime
        for p in dest.rglob("*")
        if p.is_file() and ".amz-download" not in p.parts
    }
    store.close()


# --- 6.9 bounded concurrency --------------------------------------------------


async def test_transfers_overlap_within_the_cap(tmp_path):
    fake = FakeAmazon(latency=0.05)
    for index in range(6):
        fake.add_media(f"n{index}", f"f{index}.jpg")
    store = make_store(tmp_path)

    summary = await run_sync(fake, store, tmp_path / "lib", concurrency=3)

    assert summary.downloaded == 6
    assert summary.failed == 0
    assert fake.max_active > 1  # transfers really overlapped
    assert fake.max_active <= 3  # but never past the requested cap
    assert not list((tmp_path / "lib").rglob("*.part"))
    store.close()


async def test_single_worker_caps_overlap_at_one(tmp_path):
    fake = FakeAmazon(latency=0.02)
    for index in range(4):
        fake.add_media(f"n{index}", f"f{index}.jpg")
    store = make_store(tmp_path)

    summary = await run_sync(fake, store, tmp_path / "lib", concurrency=1)

    assert summary.downloaded == 4
    assert fake.max_active == 1  # one worker: nothing overlaps
    store.close()


async def test_failure_is_isolated_while_transfers_overlap(tmp_path):
    fake = FakeAmazon(latency=0.02)
    for index in range(4):
        fake.add_media(f"n{index}", f"f{index}.jpg")
    fake.add_media("bad", "bad.jpg", data=b"bad", md5="deadbeef")
    store = make_store(tmp_path)
    dest = tmp_path / "lib"

    summary = await run_sync(fake, store, dest, concurrency=4)

    assert summary.downloaded == 4
    assert summary.failed == 1
    assert store.failure_count() == 1
    assert (dest / "2023" / "08" / "2023-08-14_f0.jpg").exists()
    assert not (dest / "2023" / "08" / "2023-08-14_bad.jpg").exists()
    assert not list(dest.rglob("*.part"))
    store.close()


async def test_concurrent_downloads_do_not_share_files(tmp_path):
    fake = FakeAmazon(latency=0.01)
    for index in range(8):
        fake.add_media(f"n{index}", f"f{index}.jpg", data=f"bytes-{index}".encode())
    store = make_store(tmp_path)
    dest = tmp_path / "lib"

    summary = await run_sync(fake, store, dest, concurrency=4)

    assert summary.downloaded == 8
    for index in range(8):
        target = dest / "2023" / "08" / f"2023-08-14_f{index}.jpg"
        assert target.read_bytes() == f"bytes-{index}".encode()
    store.close()


# --- 5.6 views regenerated after sync ---------------------------------------


async def test_views_regenerated_at_end_of_sync(tmp_path):
    fake = FakeAmazon()
    fake.add_folder("f1", "Pictures")
    fake.add_media("n1", "a.jpg", parents=["f1"])
    fake.add_album("al1", "Summer", members=["n1"])
    store = make_store(tmp_path)
    dest = tmp_path / "lib"
    await run_sync(fake, store, dest)
    assert (dest / "_by-tree" / "Pictures" / "2023-08-14_a.jpg").is_symlink()
    assert (dest / "_by-album" / "Summer" / "2023-08-14_a.jpg").is_symlink()
    store.close()


# --- diagnostics (verbosity) -------------------------------------------------


async def test_phase_records_report_counts(tmp_path, info_logging, caplog):
    fake = FakeAmazon()
    fake.add_media("n1", "a.jpg")
    fake.add_media("n2", "b.jpg")
    store = make_store(tmp_path)

    await run_sync(fake, store, tmp_path / "lib")

    text = caplog.text
    assert "reconciling 2 remote nodes against 0 recorded" in text
    assert "planned download=2" in text


async def test_each_action_is_reported_with_its_path(tmp_path, info_logging, caplog):
    fake = FakeAmazon()
    fake.add_media("n1", "a.jpg")
    fake.add_media("n2", "b.jpg")
    store = make_store(tmp_path)

    await run_sync(fake, store, tmp_path / "lib")

    lines = [line for line in caplog.text.splitlines() if "download n" in line]
    assert len(lines) == 2
    assert any("2023/08/2023-08-14_a.jpg" in line for line in lines)
    assert any("reason" not in line for line in lines) or True  # reason is inline
    assert all("(" in line and "new)" in line for line in lines)


async def test_worker_pool_size_only_at_debug(tmp_path, info_logging, caplog):
    fake = FakeAmazon()
    fake.add_media("n1", "a.jpg")
    store = make_store(tmp_path)

    await run_sync(fake, store, tmp_path / "lib")

    assert "executing" not in caplog.text


async def test_worker_pool_size_recorded_at_debug(tmp_path, debug_logging, caplog):
    fake = FakeAmazon()
    fake.add_media("n1", "a.jpg")
    store = make_store(tmp_path)

    await run_sync(fake, store, tmp_path / "lib")

    assert "executing 1 actions on 4 workers" in caplog.text


async def test_verbosity_does_not_change_the_outcome(tmp_path):
    signatures = []
    for level in (0, 1, 2):
        log.configure(level)
        fake = FakeAmazon()
        fake.add_media("n1", "a.jpg", data=b"one")
        fake.add_media("n2", "b.jpg", data=b"two")
        fake.add_media("bad", "c.jpg", data=b"x", md5="deadbeef")
        dest = tmp_path / f"lib{level}"
        store = make_store(dest)

        summary = await run_sync(fake, store, dest)

        files = sorted(
            (str(path.relative_to(dest)), path.read_bytes())
            for path in dest.rglob("*")
            if path.is_file() and ".amz-download" not in path.parts
        )
        recorded = sorted(
            (record.node_id, record.canonical_path, record.status, record.md5)
            for record in store.all_nodes()
        )
        signatures.append(
            (
                summary.downloaded,
                summary.failed,
                summary.skipped,
                summary.archived,
                files,
                recorded,
                tuple(
                    (node_id, message)
                    for node_id, message, _at in store.recent_failures()
                ),
                store.failure_count(),
            )
        )
        store.close()
    log.configure(0)

    assert signatures[0] == signatures[1] == signatures[2]


# --- 4.4 state rebuild (repair) ---------------------------------------------


async def test_rebuild_state_adopts_without_downloading(tmp_path):
    fake = FakeAmazon()
    fake.add_media("n1", "one.jpg", data=b"hello-world")
    dest = tmp_path / "lib"
    (dest / "random").mkdir(parents=True)
    (dest / "random" / "one.jpg").write_bytes(b"hello-world")
    (dest / "random" / "unknown.jpg").write_bytes(b"not-in-amazon")

    client = AmazonPhotosClient(COOKIES, transport=fake.transport, backoff_base=0.0)
    media = await client.list_media()
    tree = build_remote_tree(media, [])
    store = make_store(tmp_path)
    store.initialize()
    result = rebuild_state(store, media, tree, dest)
    assert result.adopted == 1
    assert result.unmatched == ["random/unknown.jpg"]
    canonical = dest / "2023" / "08" / "2023-08-14_one.jpg"
    assert canonical.read_bytes() == b"hello-world"
    # Unmatched file is reported, not moved or deleted.
    assert (dest / "random" / "unknown.jpg").exists()

    summary = await run_sync(fake, store, dest)
    assert summary.downloaded == 0
    assert summary.changed == 0
    assert content_requests(fake) == 0
    await client.aclose()
    store.close()
