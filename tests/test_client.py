"""Tests for task group 3 — Amazon web API client."""

from __future__ import annotations

import asyncio
import hashlib
import time

import pytest

from amz_download.client import AmazonPhotosClient, RateLimiter, determine_tld
from amz_download.errors import DownloadError, HashMismatchError, SessionExpiredError
from amz_download.models import MediaType, is_media_node, parse_album, parse_node
from fake_amazon import FakeAmazon

COOKIES = {"at_main": "a", "ubid_main": "u", "session-id": "s"}


def make_client(fake: FakeAmazon, **kwargs) -> AmazonPhotosClient:
    kwargs.setdefault("concurrency", 4)
    kwargs.setdefault("backoff_base", 0.0)
    return AmazonPhotosClient(COOKIES, transport=fake.transport, **kwargs)


# --- 3.1 models and parsing --------------------------------------------------


def test_parse_media_node():
    payload = {
        "id": "n1",
        "name": "beach.jpg",
        "kind": "FILE",
        "parents": ["f1", {"id": "f2"}],
        "contentProperties": {
            "contentType": "image/jpeg",
            "contentDate": "2023-08-14T12:00:00.000Z",
            "size": "1234",
            "md5": "abc",
        },
    }
    node = parse_node(payload)
    assert node is not None
    assert node.node_id == "n1"
    assert node.media_type is MediaType.PHOTO
    assert node.size == 1234
    assert node.parents == ("f1", "f2")
    assert node.content_date.year == 2023


def test_video_media_type_and_undated():
    payload = {
        "id": "v1",
        "name": "clip.mp4",
        "kind": "FILE",
        "contentProperties": {"contentType": "video/mp4"},
    }
    node = parse_node(payload)
    assert node is not None
    assert node.media_type is MediaType.VIDEO
    assert node.content_date is None


def test_non_media_excluded():
    document = {
        "id": "d1",
        "name": "notes.pdf",
        "kind": "FILE",
        "contentProperties": {"contentType": "application/pdf"},
    }
    folder = {"id": "f1", "name": "Pictures", "kind": "FOLDER"}
    assert is_media_node(document) is False
    assert parse_node(document) is None
    assert parse_node(folder) is None


def test_parse_album_and_tld():
    album = parse_album({"id": "al1", "name": "Summer", "kind": "VISUAL_COLLECTION"})
    assert album is not None and album.name == "Summer"
    assert determine_tld({"at_main": "x"}) == "com"
    assert determine_tld({"at-acbde": "x", "ubid-acbde": "y"}) == "de"


# --- 3.2 media and folder listing with pagination ---------------------------


async def test_list_media_paginates_all_pages():
    fake = FakeAmazon(page_size=2)
    for i in range(5):
        fake.add_media(f"n{i}", f"img{i}.jpg")
    fake.add_media("doc", "notes.pdf", content_type="application/pdf")
    client = make_client(fake, page_size=2)
    nodes = await client.list_media()
    await client.aclose()
    assert sorted(n.node_id for n in nodes) == ["n0", "n1", "n2", "n3", "n4"]
    assert fake.path_counts["/drive/v1/search"] >= 3


async def test_list_folders_paginates_all_pages():
    fake = FakeAmazon(page_size=1)
    fake.add_folder("f1", "Pictures")
    fake.add_folder("f2", "iPhone", parents=["f1"])
    fake.add_folder("f3", "Camera", parents=["f1"])
    client = make_client(fake, page_size=1)
    folders = await client.list_folders()
    await client.aclose()
    assert sorted(folder.name for folder in folders) == ["Camera", "Pictures", "iPhone"]


async def test_search_sends_required_search_context():
    fake = FakeAmazon()
    fake.add_media("n1", "a.jpg")
    client = make_client(fake)
    await client.list_media()
    await client.aclose()
    search_calls = [
        params for _, path, params in fake.requests if path.endswith("/search")
    ]
    assert search_calls
    assert all(params.get("searchContext") == "customer" for params in search_calls)


# --- 3.3 album listing and membership ---------------------------------------


async def test_list_albums_and_members():
    fake = FakeAmazon(page_size=1)
    fake.add_media("n1", "a.jpg")
    fake.add_media("n2", "b.jpg")
    fake.add_media("n3", "c.jpg")
    fake.add_album("al1", "Summer", members=["n1", "n2", "n3"])
    client = make_client(fake, page_size=1)
    albums = await client.list_albums()
    assert [a.name for a in albums] == ["Summer"]
    members = await client.album_members("al1")
    await client.aclose()
    assert sorted(members) == ["n1", "n2", "n3"]


# --- 3.4 streaming download --------------------------------------------------


async def test_download_streams_bytes_and_verifies_md5(tmp_path):
    fake = FakeAmazon()
    data = b"x" * (300 * 1024) + b"tail"
    fake.add_media("n1", "big.jpg", data=data)
    expected = hashlib.md5(data).hexdigest()
    client = make_client(fake)
    target = tmp_path / "big.jpg"
    digest = await client.download("n1", target, expected_md5=expected)
    await client.aclose()
    assert target.read_bytes() == data
    assert digest == expected
    assert hashlib.md5(target.read_bytes()).hexdigest() == expected


async def test_download_hash_mismatch_raises(tmp_path):
    fake = FakeAmazon()
    fake.add_media("n1", "img.jpg", data=b"actual")
    client = make_client(fake)
    with pytest.raises(HashMismatchError):
        await client.download("n1", tmp_path / "img.jpg", expected_md5="deadbeef")
    await client.aclose()


async def test_download_retries_transient_error(tmp_path):
    fake = FakeAmazon()
    data = b"hello" * 1000
    fake.add_media("n1", "a.jpg", data=data)
    fake.faults["/contentRedirection"] = 1
    client = make_client(fake, max_retries=4)
    target = tmp_path / "a.jpg"
    digest = await client.download("n1", target)
    await client.aclose()
    assert target.read_bytes() == data
    assert digest == hashlib.md5(data).hexdigest()
    assert fake.path_counts["/drive/v1/nodes/n1/contentRedirection"] == 2


async def test_download_gives_up_after_retries(tmp_path):
    fake = FakeAmazon()
    fake.add_media("n1", "a.jpg", data=b"x")
    fake.faults["/contentRedirection"] = 99
    client = make_client(fake, max_retries=2)
    with pytest.raises(DownloadError):
        await client.download("n1", tmp_path / "a.jpg")
    await client.aclose()


# --- 3.5 concurrency, retry --------------------------------------------------


async def test_transient_errors_retried():
    fake = FakeAmazon()
    fake.add_media("n1", "a.jpg")
    fake.faults["/drive/v1/search"] = 2
    client = make_client(fake, max_retries=4)
    nodes = await client.list_media()
    await client.aclose()
    assert len(nodes) == 1
    assert fake.path_counts["/drive/v1/search"] >= 3


async def test_concurrency_never_exceeds_cap(tmp_path):
    fake = FakeAmazon(latency=0.03)
    for i in range(8):
        fake.add_media(f"n{i}", f"img{i}.jpg", data=f"data{i}".encode() * 100)
    client = AmazonPhotosClient(
        COOKIES, transport=fake.transport, concurrency=2, backoff_base=0.0
    )

    async def one(i: int) -> None:
        await client.download(f"n{i}", tmp_path / f"img{i}.jpg")

    await asyncio.gather(*(one(i) for i in range(8)))
    await client.aclose()
    assert fake.max_active <= 2
    assert fake.max_active >= 1


async def test_rate_limiter_spaces_requests():
    limiter = RateLimiter(min_interval=0.05)
    start = time.monotonic()
    await limiter.acquire()
    await limiter.acquire()
    await limiter.acquire()
    elapsed = time.monotonic() - start
    assert elapsed >= 0.09


# --- session ----------------------------------------------------------------


async def test_check_session_rejected_raises_expired():
    fake = FakeAmazon()
    fake.on_request = lambda request, server: (
        __import__("httpx").Response(401, text="nope")
        if request.url.path.endswith("/account/usage")
        else None
    )
    client = make_client(fake)
    with pytest.raises(SessionExpiredError):
        await client.check_session()
    await client.aclose()
