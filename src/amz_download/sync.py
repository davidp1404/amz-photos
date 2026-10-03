"""Incremental one-way sync engine (design D3, D4, D8).

Three-way reconciliation of remote nodes, recorded state, and the local
filesystem produces exactly one action per node: download, refresh, skip, heal,
move, or archive. Execution is failure-isolated, resumable (through recorded
state), and non-destructive: a remote deletion archives locally rather than
deleting.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Callable, Iterable, Mapping

from .client import AmazonPhotosClient
from .errors import AmzError
from .models import Folder, Node, NodeStatus
from .paths import resolve_canonical_paths
from .state import NodeRecord, StateStore, now_iso
from .tree import RemoteEntry, build_remote_tree
from .views import ViewResult, generate_views


class ActionType(str, Enum):
    DOWNLOAD = "download"
    REFRESH = "refresh"
    SKIP = "skip"
    HEAL = "heal"
    MOVE = "move"
    ARCHIVE = "archive"


CONTENT_ACTIONS = (ActionType.DOWNLOAD, ActionType.REFRESH, ActionType.HEAL)


@dataclass(slots=True)
class Action:
    type: ActionType
    node_id: str
    node: Node | None
    canonical_path: str
    tree_path: str = ""
    tree_paths: list[str] = field(default_factory=list)
    old_canonical_path: str | None = None
    restore: bool = False
    reason: str = ""


@dataclass(slots=True)
class SyncSummary:
    listed: int = 0
    downloaded: int = 0
    refreshed: int = 0
    skipped: int = 0
    deferred: int = 0
    moved: int = 0
    healed: int = 0
    archived: int = 0
    failed: int = 0
    bytes: int = 0
    planned: int = 0
    dry_run: bool = False
    views_skipped: bool = False
    views_warning: str | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def changed(self) -> int:
        return (
            self.downloaded + self.refreshed + self.moved + self.healed + self.archived
        )

    @property
    def ok(self) -> bool:
        return self.failed == 0


@dataclass(slots=True)
class RepairResult:
    adopted: int = 0
    unmatched: list[str] = field(default_factory=list)


# --- reconciliation ----------------------------------------------------------


def reconcile(
    remote_nodes: Iterable[Node],
    remote_tree: Mapping[str, RemoteEntry],
    canonical_paths: Mapping[str, str],
    recorded: Mapping[str, NodeRecord],
    dest: str | os.PathLike[str],
    *,
    file_exists: Callable[[str], bool] | None = None,
) -> list[Action]:
    """Produce exactly one action per remote node plus archives for deletions."""
    dest = Path(dest)
    exists = file_exists or (lambda relative: (dest / relative).is_file())
    actions: list[Action] = []
    remote_ids: set[str] = set()

    for node in remote_nodes:
        node_id = node.node_id
        remote_ids.add(node_id)
        entry = remote_tree.get(node_id)
        tree_paths = list(entry.tree_paths) if entry else [""]
        tree_path = entry.primary_tree_path if entry else ""
        canonical = canonical_paths[node_id]
        record = recorded.get(node_id)
        common = {
            "node_id": node_id,
            "node": node,
            "canonical_path": canonical,
            "tree_path": tree_path,
            "tree_paths": tree_paths,
        }

        if record is None:
            actions.append(Action(ActionType.DOWNLOAD, reason="new", **common))
            continue

        if record.status == NodeStatus.ARCHIVED.value:
            same_content = node.md5 is not None and node.md5 == record.md5
            if same_content and exists(record.canonical_path):
                actions.append(
                    Action(
                        ActionType.SKIP,
                        old_canonical_path=record.canonical_path,
                        restore=True,
                        reason="reappeared",
                        **common,
                    )
                )
            elif not same_content:
                actions.append(
                    Action(
                        ActionType.REFRESH,
                        old_canonical_path=record.canonical_path,
                        restore=True,
                        reason="reappeared with new content",
                        **common,
                    )
                )
            else:
                actions.append(
                    Action(
                        ActionType.HEAL,
                        old_canonical_path=record.canonical_path,
                        restore=True,
                        reason="reappeared but local file missing",
                        **common,
                    )
                )
            continue

        content_changed = (
            node.md5 is not None and record.md5 is not None and node.md5 != record.md5
        )
        path_changed = canonical != record.canonical_path
        new_exists = exists(canonical)
        old_exists = exists(record.canonical_path)

        if content_changed:
            actions.append(
                Action(
                    ActionType.REFRESH,
                    old_canonical_path=record.canonical_path,
                    reason="content changed",
                    **common,
                )
            )
        elif path_changed and old_exists:
            actions.append(
                Action(
                    ActionType.MOVE,
                    old_canonical_path=record.canonical_path,
                    reason="remote path changed",
                    **common,
                )
            )
        elif path_changed and new_exists:
            actions.append(
                Action(
                    ActionType.SKIP,
                    old_canonical_path=record.canonical_path,
                    reason="already at new path",
                    **common,
                )
            )
        elif not new_exists:
            actions.append(
                Action(ActionType.HEAL, reason="local file missing", **common)
            )
        else:
            actions.append(Action(ActionType.SKIP, reason="unchanged", **common))

    for record in recorded.values():
        if record.node_id in remote_ids:
            continue
        if record.status == NodeStatus.PRESENT.value:
            actions.append(
                Action(
                    ActionType.ARCHIVE,
                    node_id=record.node_id,
                    node=None,
                    canonical_path=record.canonical_path,
                    tree_path=record.tree_path,
                    tree_paths=list(record.tree_paths),
                    reason="remote deleted",
                )
            )

    return actions


# --- execution ---------------------------------------------------------------


def set_mtime(path: str | os.PathLike[str], content_date: datetime | None) -> None:
    """Set a stored file's modification time to its content date (task 5.2)."""
    if content_date is None:
        return
    timestamp = content_date.timestamp()
    os.utime(path, (timestamp, timestamp))


async def execute_plan(
    plan: Iterable[Action],
    *,
    client: AmazonPhotosClient,
    store: StateStore,
    dest: str | os.PathLike[str],
    run_id: str | None = None,
    dry_run: bool = False,
    limit: int | None = None,
    warn: Callable[[str], None] | None = None,
) -> SyncSummary:
    dest = Path(dest)
    summary = SyncSummary(dry_run=dry_run)
    content_done = 0

    for action in plan:
        if action.type in CONTENT_ACTIONS:
            if limit is not None and content_done >= limit:
                summary.deferred += 1
                continue
            content_done += 1
        try:
            await _apply_action(
                action,
                client=client,
                store=store,
                dest=dest,
                run_id=run_id,
                dry_run=dry_run,
                summary=summary,
            )
        except (AmzError, OSError) as exc:
            summary.failed += 1
            message = f"{action.node_id}: {exc}"
            summary.errors.append(message)
            if warn:
                warn(message)
            if not dry_run:
                store.record_failure(action.node_id, str(exc), run_id)
            _discard_partial(dest / action.canonical_path)
    return summary


async def _apply_action(
    action: Action,
    *,
    client: AmazonPhotosClient,
    store: StateStore,
    dest: Path,
    run_id: str | None,
    dry_run: bool,
    summary: SyncSummary,
) -> None:
    target = dest / action.canonical_path

    if action.type in CONTENT_ACTIONS:
        if dry_run:
            summary.planned += 1
            _count_content(summary, action.type)
            return
        assert action.node is not None
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + ".part")
        try:
            await client.download(
                action.node.node_id, partial, expected_md5=action.node.md5
            )
            os.replace(partial, target)
        except BaseException:
            _discard_partial(target)
            raise
        set_mtime(target, action.node.content_date)
        if (
            action.old_canonical_path
            and action.old_canonical_path != action.canonical_path
        ):
            old = dest / action.old_canonical_path
            if old.exists() and old.resolve() != target.resolve():
                old.unlink()
        record = NodeRecord.from_node(
            action.node,
            canonical_path=action.canonical_path,
            tree_path=action.tree_path,
            tree_paths=action.tree_paths,
            status=NodeStatus.PRESENT,
            downloaded_at=now_iso(),
        )
        store.upsert_node(record)
        _count_content(summary, action.type)
        summary.bytes += action.node.size or (
            target.stat().st_size if target.exists() else 0
        )
        return

    if action.type is ActionType.MOVE:
        if dry_run:
            summary.planned += 1
            summary.moved += 1
            return
        assert action.old_canonical_path
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(dest / action.old_canonical_path, target)
        store.update_location(
            action.node_id,
            canonical_path=action.canonical_path,
            tree_path=action.tree_path,
            tree_paths=action.tree_paths,
        )
        summary.moved += 1
        return

    if action.type is ActionType.ARCHIVE:
        if dry_run:
            summary.planned += 1
            summary.archived += 1
            return
        store.mark_archived(action.node_id)
        summary.archived += 1
        return

    # SKIP
    summary.skipped += 1
    if dry_run:
        return
    if action.restore:
        store.clear_archived(action.node_id)
    store.update_location(
        action.node_id,
        canonical_path=action.canonical_path,
        tree_path=action.tree_path,
        tree_paths=action.tree_paths,
    )


def _count_content(summary: SyncSummary, action_type: ActionType) -> None:
    if action_type is ActionType.DOWNLOAD:
        summary.downloaded += 1
    elif action_type is ActionType.REFRESH:
        summary.refreshed += 1
    else:
        summary.healed += 1


def _discard_partial(target: Path) -> None:
    partial = target.with_name(target.name + ".part")
    if partial.exists():
        try:
            partial.unlink()
        except OSError:
            pass


# --- orchestration -----------------------------------------------------------


async def sync(
    client: AmazonPhotosClient,
    store: StateStore,
    dest: str | os.PathLike[str],
    *,
    dry_run: bool = False,
    limit: int | None = None,
    generate_views_after: bool = True,
    warn: Callable[[str], None] | None = None,
) -> SyncSummary:
    """Run one incremental sync. Validates the session before any writes."""
    dest = Path(dest)
    await client.check_session()

    media = await client.list_media()
    folders: list[Folder] = await client.list_folders()
    albums = await client.list_albums()
    memberships = {
        album.album_id: await client.album_members(album.album_id) for album in albums
    }
    remote_tree = build_remote_tree(media, folders)
    canonical = {
        node_id: str(path) for node_id, path in resolve_canonical_paths(media).items()
    }

    if not dry_run:
        store.initialize()
    recorded = store.nodes_map() if store.path.exists() else {}

    run_id: str | None = None
    if not dry_run:
        run_id = store.start_run()
        for album in albums:
            store.upsert_album(album)
        for album in albums:
            store.replace_album_members(
                album.album_id, memberships.get(album.album_id, [])
            )

    plan = reconcile(media, remote_tree, canonical, recorded, dest)
    summary = await execute_plan(
        plan,
        client=client,
        store=store,
        dest=dest,
        run_id=run_id,
        dry_run=dry_run,
        limit=limit,
        warn=warn,
    )
    summary.listed = len(media)

    if not dry_run:
        if generate_views_after:
            result: ViewResult = generate_views(store, dest, warn=warn)
            summary.views_skipped = result.skipped
            summary.views_warning = result.warnings[0] if result.warnings else None
        store.finish_run(
            run_id,
            listed=summary.listed,
            downloaded=summary.downloaded,
            refreshed=summary.refreshed,
            skipped=summary.skipped,
            moved=summary.moved,
            healed=summary.healed,
            archived=summary.archived,
            failed=summary.failed,
            bytes=summary.bytes,
        )
    return summary


# --- state rebuild (repair) --------------------------------------------------


def _md5_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.md5()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def rebuild_state(
    store: StateStore,
    remote_nodes: Iterable[Node],
    remote_tree: Mapping[str, RemoteEntry],
    dest: str | os.PathLike[str],
    *,
    hash_file: Callable[[Path], str] | None = None,
    move_into_place: bool = True,
) -> RepairResult:
    """Rebuild recorded state from local files matched to remote nodes by hash.

    Adopted files are recorded without downloading. Files that match no remote
    node are reported and left untouched.
    """
    dest = Path(dest)
    nodes = list(remote_nodes)
    canonical_paths = resolve_canonical_paths(nodes)
    by_md5: dict[str, list[Node]] = {}
    for node in nodes:
        if node.md5:
            by_md5.setdefault(node.md5.lower(), []).append(node)
    for candidates in by_md5.values():
        candidates.sort(key=lambda n: n.node_id)

    excluded = {".amz-download", "_by-tree", "_by-album"}
    files = sorted(
        (
            path
            for path in dest.rglob("*")
            if path.is_file()
            and not path.name.endswith(".part")
            and not any(part in excluded for part in path.relative_to(dest).parts)
        ),
        key=str,
    )

    result = RepairResult()
    adopted: set[str] = set()
    for path in files:
        digest = (hash_file or _md5_file)(path)
        candidates = by_md5.get(digest.lower())
        node: Node | None = None
        while candidates:
            candidate = candidates.pop(0)
            if candidate.node_id not in adopted:
                node = candidate
                break
        if node is None:
            result.unmatched.append(str(path.relative_to(dest)))
            continue
        adopted.add(node.node_id)
        canonical = str(canonical_paths[node.node_id])
        target = dest / canonical
        if move_into_place and path.resolve() != target.resolve():
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(path, target)
        entry = remote_tree.get(node.node_id)
        store.upsert_node(
            NodeRecord.from_node(
                node,
                canonical_path=canonical,
                tree_path=entry.primary_tree_path if entry else "",
                tree_paths=entry.tree_paths if entry else [""],
                status=NodeStatus.PRESENT,
                downloaded_at=now_iso(),
            )
        )
        result.adopted += 1
    return result
