"""SQLite sync state store, keyed by Amazon ``node_id`` (design D2).

Transactional, crash-safe, stdlib-only. Holds the recorded library, album
membership, run summaries, and failures.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .models import Album, MediaType, Node, NodeStatus

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
    node_id        TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    md5            TEXT,
    size           INTEGER,
    content_date   TEXT,
    media_type     TEXT NOT NULL,
    content_type   TEXT,
    tree_path      TEXT NOT NULL DEFAULT '',
    tree_paths     TEXT NOT NULL DEFAULT '[]',
    canonical_path TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'present',
    last_seen      TEXT,
    downloaded_at  TEXT
);
CREATE TABLE IF NOT EXISTS albums (
    album_id TEXT PRIMARY KEY,
    name     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS album_members (
    album_id TEXT NOT NULL,
    node_id  TEXT NOT NULL,
    PRIMARY KEY (album_id, node_id)
);
CREATE TABLE IF NOT EXISTS runs (
    run_id       TEXT PRIMARY KEY,
    started_at   TEXT NOT NULL,
    ended_at     TEXT,
    listed       INTEGER NOT NULL DEFAULT 0,
    downloaded   INTEGER NOT NULL DEFAULT 0,
    refreshed    INTEGER NOT NULL DEFAULT 0,
    skipped      INTEGER NOT NULL DEFAULT 0,
    moved        INTEGER NOT NULL DEFAULT 0,
    healed       INTEGER NOT NULL DEFAULT 0,
    archived     INTEGER NOT NULL DEFAULT 0,
    failed       INTEGER NOT NULL DEFAULT 0,
    bytes        INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS failures (
    node_id TEXT NOT NULL,
    run_id  TEXT,
    message TEXT NOT NULL,
    at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_nodes_status ON nodes(status);
CREATE INDEX IF NOT EXISTS idx_album_members_node ON album_members(node_id);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_state_path(dest: str | os.PathLike[str]) -> Path:
    """Location of the state database inside a destination root (design D5)."""
    return Path(dest) / ".amz-download" / "state.sqlite"


def _media_type_value(value: MediaType | str) -> str:
    return value.value if isinstance(value, MediaType) else str(value)


@dataclass(slots=True)
class NodeRecord:
    node_id: str
    name: str
    md5: str | None
    size: int | None
    content_date: str | None
    media_type: str
    content_type: str | None
    tree_path: str
    tree_paths: list[str]
    canonical_path: str
    status: str = NodeStatus.PRESENT.value
    last_seen: str | None = None
    downloaded_at: str | None = None

    @classmethod
    def from_node(
        cls,
        node: Node,
        *,
        canonical_path: str,
        tree_path: str = "",
        tree_paths: Iterable[str] | None = None,
        status: NodeStatus | str = NodeStatus.PRESENT,
        downloaded_at: str | None = None,
    ) -> "NodeRecord":
        paths = list(tree_paths) if tree_paths is not None else [tree_path]
        return cls(
            node_id=node.node_id,
            name=node.name,
            md5=node.md5,
            size=node.size,
            content_date=node.content_date.isoformat() if node.content_date else None,
            media_type=_media_type_value(node.media_type),
            content_type=node.content_type,
            tree_path=tree_path,
            tree_paths=json.dumps(paths),
            canonical_path=canonical_path,
            status=status.value if isinstance(status, NodeStatus) else str(status),
            last_seen=now_iso(),
            downloaded_at=downloaded_at,
        )


@dataclass(slots=True)
class RunRecord:
    run_id: str
    started_at: str
    ended_at: str | None
    listed: int
    downloaded: int
    refreshed: int
    skipped: int
    moved: int
    healed: int
    archived: int
    failed: int
    bytes: int

    @property
    def changed(self) -> int:
        return (
            self.downloaded + self.refreshed + self.moved + self.healed + self.archived
        )


class StateStore:
    """Thin wrapper around a SQLite database holding sync state."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self._conn: sqlite3.Connection | None = None

    # --- lifecycle -----------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(self.path)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys = ON")
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "StateStore":
        self.initialize()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def initialize(self) -> None:
        conn = self._connect()
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        with conn:
            if version < 1:
                conn.executescript(SCHEMA)
                conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self._migrate(version)

    def _migrate(
        self, from_version: int
    ) -> None:  # pragma: no cover - no migrations yet
        """Hook for future schema migrations (from_version -> SCHEMA_VERSION)."""
        return

    # --- nodes ---------------------------------------------------------------

    def upsert_node(self, record: NodeRecord) -> None:
        logger.debug("upserting node %s at %s", record.node_id, record.canonical_path)
        conn = self._connect()
        with conn:
            conn.execute(
                """
                INSERT INTO nodes (node_id, name, md5, size, content_date, media_type,
                                   content_type, tree_path, tree_paths, canonical_path,
                                   status, last_seen, downloaded_at)
                VALUES (:node_id, :name, :md5, :size, :content_date, :media_type,
                        :content_type, :tree_path, :tree_paths, :canonical_path,
                        :status, :last_seen, :downloaded_at)
                ON CONFLICT(node_id) DO UPDATE SET
                    name=excluded.name, md5=excluded.md5, size=excluded.size,
                    content_date=excluded.content_date, media_type=excluded.media_type,
                    content_type=excluded.content_type, tree_path=excluded.tree_path,
                    tree_paths=excluded.tree_paths, canonical_path=excluded.canonical_path,
                    status=excluded.status, last_seen=excluded.last_seen,
                    downloaded_at=excluded.downloaded_at
                """,
                {
                    "node_id": record.node_id,
                    "name": record.name,
                    "md5": record.md5,
                    "size": record.size,
                    "content_date": record.content_date,
                    "media_type": record.media_type,
                    "content_type": record.content_type,
                    "tree_path": record.tree_path,
                    "tree_paths": record.tree_paths,
                    "canonical_path": record.canonical_path,
                    "status": record.status,
                    "last_seen": record.last_seen,
                    "downloaded_at": record.downloaded_at,
                },
            )

    def get_node(self, node_id: str) -> NodeRecord | None:
        row = (
            self._connect()
            .execute("SELECT * FROM nodes WHERE node_id = ?", (node_id,))
            .fetchone()
        )
        return _row_to_record(row) if row else None

    def all_nodes(self) -> list[NodeRecord]:
        rows = self._connect().execute("SELECT * FROM nodes").fetchall()
        return [_row_to_record(row) for row in rows]

    def nodes_map(self) -> dict[str, NodeRecord]:
        return {record.node_id: record for record in self.all_nodes()}

    def nodes_by_status(self, status: NodeStatus | str) -> list[NodeRecord]:
        value = status.value if isinstance(status, NodeStatus) else str(status)
        rows = (
            self._connect()
            .execute("SELECT * FROM nodes WHERE status = ?", (value,))
            .fetchall()
        )
        return [_row_to_record(row) for row in rows]

    def mark_archived(self, node_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE nodes SET status = ? WHERE node_id = ?",
                (NodeStatus.ARCHIVED.value, node_id),
            )

    def clear_archived(self, node_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE nodes SET status = ? WHERE node_id = ?",
                (NodeStatus.PRESENT.value, node_id),
            )

    def update_location(
        self,
        node_id: str,
        *,
        canonical_path: str,
        tree_path: str | None = None,
        tree_paths: Iterable[str] | None = None,
    ) -> None:
        assignments = ["canonical_path = ?"]
        values: list[object] = [canonical_path]
        if tree_path is not None:
            assignments.append("tree_path = ?")
            values.append(tree_path)
        if tree_paths is not None:
            assignments.append("tree_paths = ?")
            values.append(json.dumps(list(tree_paths)))
        values.append(node_id)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE nodes SET {', '.join(assignments)} WHERE node_id = ?", values
            )

    # --- albums --------------------------------------------------------------

    def upsert_album(self, album: Album) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO albums (album_id, name) VALUES (?, ?) "
                "ON CONFLICT(album_id) DO UPDATE SET name=excluded.name",
                (album.album_id, album.name),
            )

    def all_albums(self) -> list[Album]:
        rows = self._connect().execute("SELECT * FROM albums").fetchall()
        return [Album(album_id=row["album_id"], name=row["name"]) for row in rows]

    def replace_album_members(self, album_id: str, node_ids: Iterable[str]) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM album_members WHERE album_id = ?", (album_id,))
            conn.executemany(
                "INSERT OR IGNORE INTO album_members (album_id, node_id) VALUES (?, ?)",
                [(album_id, node_id) for node_id in node_ids],
            )

    def album_members(self, album_id: str) -> list[str]:
        rows = (
            self._connect()
            .execute(
                "SELECT node_id FROM album_members WHERE album_id = ?", (album_id,)
            )
            .fetchall()
        )
        return [row["node_id"] for row in rows]

    def all_album_members(self) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        for row in (
            self._connect()
            .execute("SELECT album_id, node_id FROM album_members")
            .fetchall()
        ):
            result.setdefault(row["album_id"], []).append(row["node_id"])
        return result

    def node_albums(self, node_id: str) -> list[str]:
        rows = (
            self._connect()
            .execute("SELECT album_id FROM album_members WHERE node_id = ?", (node_id,))
            .fetchall()
        )
        return [row["album_id"] for row in rows]

    # --- runs ----------------------------------------------------------------

    def start_run(self) -> str:
        run_id = uuid.uuid4().hex
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO runs (run_id, started_at) VALUES (?, ?)",
                (run_id, now_iso()),
            )
        return run_id

    def finish_run(self, run_id: str, **counts: int) -> None:
        fields = [
            "downloaded",
            "refreshed",
            "skipped",
            "moved",
            "healed",
            "archived",
            "failed",
            "listed",
            "bytes",
        ]
        assignments = [f"{name} = ?" for name in fields]
        values = [int(counts.get(name, 0)) for name in fields]
        values.extend([now_iso(), run_id])
        with self._connect() as conn:
            conn.execute(
                f"UPDATE runs SET {', '.join(assignments)}, ended_at = ? WHERE run_id = ?",
                values,
            )

    def last_run(self) -> RunRecord | None:
        row = (
            self._connect()
            .execute("SELECT * FROM runs ORDER BY started_at DESC, rowid DESC LIMIT 1")
            .fetchone()
        )
        return _row_to_run(row) if row else None

    # --- failures ------------------------------------------------------------

    def record_failure(
        self, node_id: str, message: str, run_id: str | None = None
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO failures (node_id, run_id, message, at) VALUES (?, ?, ?, ?)",
                (node_id, run_id, message, now_iso()),
            )

    def clear_failures(self) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM failures")

    def failure_count(self) -> int:
        return int(
            self._connect().execute("SELECT COUNT(*) FROM failures").fetchone()[0]
        )

    def recent_failures(self, limit: int = 20) -> list[tuple[str, str, str]]:
        rows = (
            self._connect()
            .execute(
                "SELECT node_id, message, at FROM failures ORDER BY at DESC LIMIT ?",
                (limit,),
            )
            .fetchall()
        )
        return [(row["node_id"], row["message"], row["at"]) for row in rows]

    # --- reconciliation helpers ---------------------------------------------

    def absent_from_remote(self, remote_ids: set[str]) -> list[str]:
        """Recorded present nodes no longer in a remote listing (to archive)."""
        return [
            record.node_id
            for record in self.nodes_by_status(NodeStatus.PRESENT)
            if record.node_id not in remote_ids
        ]

    def archived_but_present(self, remote_ids: set[str]) -> list[str]:
        """Nodes marked remotely deleted that reappear (to restore)."""
        return [
            record.node_id
            for record in self.nodes_by_status(NodeStatus.ARCHIVED)
            if record.node_id in remote_ids
        ]


def _row_to_record(row: sqlite3.Row) -> NodeRecord:
    return NodeRecord(
        node_id=row["node_id"],
        name=row["name"],
        md5=row["md5"],
        size=row["size"],
        content_date=row["content_date"],
        media_type=row["media_type"],
        content_type=row["content_type"],
        tree_path=row["tree_path"],
        tree_paths=json.loads(row["tree_paths"]) if row["tree_paths"] else [],
        canonical_path=row["canonical_path"],
        status=row["status"],
        last_seen=row["last_seen"],
        downloaded_at=row["downloaded_at"],
    )


def _row_to_run(row: sqlite3.Row) -> RunRecord:
    return RunRecord(
        run_id=row["run_id"],
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        listed=row["listed"],
        downloaded=row["downloaded"],
        refreshed=row["refreshed"],
        skipped=row["skipped"],
        moved=row["moved"],
        healed=row["healed"],
        archived=row["archived"],
        failed=row["failed"],
        bytes=row["bytes"],
    )


def status_report(store: StateStore) -> dict[str, object]:
    """Summarise recorded state without contacting Amazon (task 7.4).

    Never creates the database: an absent state file yields a zero report.
    """
    if not store.path.exists():
        return {
            "present": 0,
            "archived": 0,
            "albums": 0,
            "failures": 0,
            "last_run": None,
        }
    return {
        "present": len(store.nodes_by_status(NodeStatus.PRESENT)),
        "archived": len(store.nodes_by_status(NodeStatus.ARCHIVED)),
        "albums": len(store.all_albums()),
        "failures": store.failure_count(),
        "last_run": store.last_run(),
    }
