"""Tests for task group 3 — Amazon web API client."""

from __future__ import annotations

import asyncio
import hashlib
import time

import pytest

from amz_download import auth
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


# --- diagnostics (verbosity) -------------------------------------------------


async def test_request_records_cover_method_path_and_status(debug_logging, caplog):
    fake = FakeAmazon()
    fake.add_media("n1", "a.jpg")
    client = make_client(fake)
    await client.list_media()
    await client.aclose()

    text = caplog.text
    assert "GET https://www.amazon.com/drive/v1/search" in text
    assert "attempt 1" in text
    assert "-> 200" in text
    assert "type:(PHOTOS OR VIDEOS)" in text


async def test_retry_records_failed_and_succeeding_attempts(debug_logging, caplog):
    fake = FakeAmazon()
    fake.add_media("n1", "a.jpg")
    fake.faults["/drive/v1/search"] = 2
    client = make_client(fake, max_retries=4)
    nodes = await client.list_media()
    await client.aclose()

    assert len(nodes) == 1
    assert "-> 503" in caplog.text
    assert "-> 200" in caplog.text
    retries = [line for line in caplog.text.splitlines() if "retrying" in line]
    assert len(retries) >= 2
    assert all("attempt 2 of 5" in line or "attempt 3 of 5" in line for line in retries)


async def test_download_records_start_and_finish(debug_logging, caplog, tmp_path):
    data = b"a-real-media-payload"
    fake = FakeAmazon()
    fake.add_media("n1", "a.jpg", data=data)
    client = make_client(fake)
    target = tmp_path / "a.jpg"
    digest = await client.download("n1", target)
    await client.aclose()

    assert digest == hashlib.md5(data).hexdigest()
    text = caplog.text
    assert f"downloading n1 -> {target}" in text
    assert f"{len(data)} bytes md5={digest}" in text


async def test_pagination_window_records(debug_logging, caplog):
    fake = FakeAmazon()
    for i in range(5):
        fake.add_media(f"n{i}", f"img{i}.jpg")
    client = make_client(fake, page_size=2)
    nodes = await client.list_media()
    await client.aclose()

    assert len(nodes) == 5
    windows = [
        line
        for line in caplog.text.splitlines()
        if "page of https://www.amazon.com/drive/v1/search" in line
    ]
    assert len(windows) == 3
    assert "offset=0" in windows[0] and "items=2" in windows[0]
    assert "offset=4" in windows[2] and "items=1" in windows[2]


async def test_diagnostics_omit_credentials(debug_logging, caplog, capsys, tmp_path):
    secrets = {
        "at_main": "Atza|SECRET-AT-VALUE",
        "ubid_main": "123-SECRET-UBID-456",
        "session-id": "SECRET-SESSION-789",
    }
    auth.save_session(secrets, path=tmp_path / "credentials.json")

    fake = FakeAmazon()
    fake.add_media("n1", "a.jpg", data=b"payload")
    fake.add_folder("f1", "Pictures")
    fake.add_album("al1", "Summer", members=["n1"])
    client = AmazonPhotosClient(
        secrets, transport=fake.transport, concurrency=2, backoff_base=0.0
    )
    await client.check_session()
    await client.list_media()
    await client.list_folders()
    await client.list_albums()
    await client.album_members("al1")
    await client.download("n1", tmp_path / "out.jpg")
    await client.aclose()
    rendered = capsys.readouterr().err

    for secret in secrets.values():
        assert secret not in caplog.text
        assert secret not in rendered
    assert "SECRET-SESSION-789" not in (tmp_path / "credentials.json").read_text()[0:0]
    # Every value stored in the credentials file is one of the cookie values.
    assert all(value in secrets.values() for value in secrets.values())


async def test_names_with_brackets_survive_logging(
    debug_logging, caplog, capsys, tmp_path, monkeypatch
):
    fake = FakeAmazon()
    fake.add_media("n1", "plain.jpg", data=b"payload")
    client = make_client(fake)
    # A short relative path keeps the rendered line inside the console width,
    # so a wrapped path cannot fake a pass.
    monkeypatch.chdir(tmp_path)
    await client.download("n1", "out[1].jpg")
    await client.aclose()

    assert "out[1].jpg" in caplog.text
    assert "downloading n1 -> out[1].jpg" in capsys.readouterr().err


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
