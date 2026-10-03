"""Tests for task 5.1 — canonical path builder."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import PurePosixPath

from amz_download.models import MediaType, Node
from amz_download.paths import (
    base_relative_path,
    resolve_canonical_paths,
    sanitize_component,
    with_collision_suffix,
)


def node(node_id, name, *, date="2023-08-14T12:00:00Z", ctype="image/jpeg"):
    content_date = (
        datetime.fromisoformat(date.replace("Z", "+00:00")).astimezone(timezone.utc)
        if date is not None
        else None
    )
    return Node(
        node_id=node_id,
        name=name,
        media_type=MediaType.PHOTO,
        content_type=ctype,
        content_date=content_date,
    )


def test_normal_path_uses_content_date_and_name():
    path = base_relative_path(node("n1", "beach.jpg"))
    assert path == PurePosixPath("2023/08/2023-08-14_beach.jpg")


def test_missing_date_uses_deterministic_fallback():
    undated = node("n1", "mystery.jpg", date=None)
    assert base_relative_path(undated) == PurePosixPath("_unsorted/mystery.jpg")
    assert base_relative_path(undated) == base_relative_path(undated)


def test_extension_derived_from_content_type_when_absent():
    path = base_relative_path(node("n1", "noextension", ctype="video/mp4"))
    assert path == PurePosixPath("2023/08/2023-08-14_noextension.mp4")


def test_collision_disambiguated_deterministically():
    a = node("nodeAAAA1111", "same.jpg")
    b = node("nodeBBBB2222", "same.jpg")
    resolved = resolve_canonical_paths([b, a])  # order must not matter
    assert resolved["nodeAAAA1111"] == PurePosixPath("2023/08/2023-08-14_same.jpg")
    assert resolved["nodeBBBB2222"] == PurePosixPath(
        "2023/08/2023-08-14_same_BBBB2222.jpg"
    )
    again = resolve_canonical_paths([a, b])
    assert resolved == again


def test_with_collision_suffix_inserts_before_extension():
    base = PurePosixPath("2023/08/2023-08-14_same.jpg")
    assert with_collision_suffix(base, "abcdEFGH1234") == PurePosixPath(
        "2023/08/2023-08-14_same_EFGH1234.jpg"
    )


def test_sanitize_component_removes_separators():
    assert sanitize_component("a/b\\c") == "a_b_c"
    assert sanitize_component("") == "_"
