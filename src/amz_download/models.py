"""Domain models for Amazon Photos nodes and albums, and payload parsing.

Media is limited to photos and videos (design non-goals exclude documents,
archives, and audio). The parsing functions here are the only place that
interprets Amazon's raw JSON payloads.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class MediaType(str, Enum):
    PHOTO = "photo"
    VIDEO = "video"


class NodeStatus(str, Enum):
    PRESENT = "present"
    ARCHIVED = "archived"  # present locally, absent from a remote listing


@dataclass(slots=True)
class Node:
    """A media node (FILE) parsed from an Amazon payload."""

    node_id: str
    name: str
    media_type: MediaType
    content_date: datetime | None = None
    size: int | None = None
    md5: str | None = None
    content_type: str | None = None
    parents: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_dated(self) -> bool:
        return self.content_date is not None


@dataclass(slots=True)
class Album:
    album_id: str
    name: str


@dataclass(slots=True)
class Folder:
    node_id: str
    name: str
    parents: tuple[str, ...] = field(default_factory=tuple)


def _as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_content_date(value: Any) -> datetime | None:
    """Parse an Amazon date into a timezone-aware UTC datetime.

    Accepts ISO-8601 strings (with or without a trailing ``Z``) and epoch
    milliseconds (int or numeric string). Returns ``None`` when absent or
    unparseable, which routes the node to the undated fallback location.
    """
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return _from_epoch_ms(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        # Epoch milliseconds supplied as a string.
        if text.isdigit():
            return _from_epoch_ms(int(text))
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    return None


def _from_epoch_ms(value: float) -> datetime | None:
    try:
        return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def media_type_for(content_type: str | None) -> MediaType | None:
    """Return the media type for a MIME content type, or ``None`` if non-media."""
    if not content_type:
        return None
    lowered = content_type.lower()
    if lowered.startswith("image/"):
        return MediaType.PHOTO
    if lowered.startswith("video/"):
        return MediaType.VIDEO
    return None


def _extract_parents(raw: Any) -> tuple[str, ...]:
    """Normalise a ``parents`` field that may be ids or ``{"id": ...}`` dicts."""
    if not raw:
        return ()
    parents: list[str] = []
    for item in raw:
        if isinstance(item, str):
            parents.append(item)
        elif isinstance(item, dict):
            ident = item.get("id") or item.get("nodeId")
            if ident:
                parents.append(str(ident))
    return tuple(parents)


def node_id_of(payload: dict[str, Any]) -> str | None:
    node_id = payload.get("id") or payload.get("nodeId")
    if not node_id and isinstance(payload.get("info"), dict):
        node_id = payload["info"].get("nodeId")
    return str(node_id) if node_id else None


def is_media_node(payload: dict[str, Any]) -> bool:
    """Whether a raw payload is a samplable media file (photo or video)."""
    if payload.get("kind") not in (None, "FILE"):
        return False
    content_type = (payload.get("contentProperties") or {}).get("contentType")
    return media_type_for(content_type) is not None


def parse_node(payload: dict[str, Any]) -> Node | None:
    """Parse a media node payload, or return ``None`` for non-media nodes."""
    media_type = media_type_for(
        (payload.get("contentProperties") or {}).get("contentType")
    )
    if media_type is None or payload.get("kind") not in (None, "FILE"):
        return None
    node_id = node_id_of(payload)
    if not node_id:
        return None
    props = payload.get("contentProperties") or {}
    content_date = parse_content_date(
        props.get("contentDate") or payload.get("createdDate")
    )
    return Node(
        node_id=node_id,
        name=str(payload.get("name") or node_id),
        media_type=media_type,
        content_date=content_date,
        size=_as_int(props.get("size")),
        md5=(props.get("md5") or None),
        content_type=props.get("contentType"),
        parents=_extract_parents(payload.get("parents")),
    )


def parse_album(payload: dict[str, Any]) -> Album | None:
    album_id = node_id_of(payload)
    if not album_id:
        return None
    return Album(album_id=album_id, name=str(payload.get("name") or album_id))


def parse_folder(payload: dict[str, Any]) -> Folder | None:
    """Parse a folder payload."""
    node_id = node_id_of(payload)
    if not node_id:
        return None
    return Folder(
        node_id=node_id,
        name=str(payload.get("name") or ""),
        parents=_extract_parents(payload.get("parents")),
    )
