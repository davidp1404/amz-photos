"""Tests for task group 7 — command-line interface."""

from __future__ import annotations

import io

import httpx
import pytest
from rich.console import Console
from typer.testing import CliRunner

from amz_download import auth, cli
from amz_download.client import AmazonPhotosClient
from amz_download.models import MediaType, Node, NodeStatus
from amz_download.state import NodeRecord, StateStore, default_state_path
from fake_amazon import FakeAmazon

COOKIES = {"at_main": "a", "ubid_main": "u", "session-id": "s"}
runner = CliRunner()


@pytest.fixture
def captured_console(monkeypatch):
    out = io.StringIO()
    err = io.StringIO()
    monkeypatch.setattr(cli, "console", Console(file=out, width=200))
    monkeypatch.setattr(cli, "err_console", Console(file=err, width=200))
    return out, err


# --- 7.1 help for every subcommand ------------------------------------------


@pytest.mark.parametrize(
    "command", ["login", "check", "sync", "views", "status", "repair"]
)
def test_subcommand_help(command):
    result = runner.invoke(cli.app, [command, "--help"])
    assert result.exit_code == 0


def test_root_help_lists_subcommands():
    result = runner.invoke(cli.app, ["--help"])
    assert result.exit_code == 0
    for command in ("login", "check", "sync", "views", "status", "repair"):
        assert command in result.output


# --- 7.2 non-interactive sync and exit code ---------------------------------


def test_sync_without_session_fails_nonzero_and_does_not_prompt(tmp_path, monkeypatch):
    monkeypatch.setenv("AMZ_DOWNLOAD_CREDENTIALS", str(tmp_path / "absent.json"))
    result = runner.invoke(cli.app, ["sync", "--dest", str(tmp_path / "lib")])
    assert result.exit_code == 2
    assert "login" in result.output
    assert not (tmp_path / "lib").exists()


# --- 7.3 progress and summary output ----------------------------------------


def test_sync_prints_counts(tmp_path, monkeypatch, captured_console):
    out, _ = captured_console
    credentials = tmp_path / "credentials.json"
    auth.save_session(COOKIES, path=credentials)
    monkeypatch.setenv("AMZ_DOWNLOAD_CREDENTIALS", str(credentials))

    fake = FakeAmazon()
    fake.add_media("n1", "a.jpg")
    fake.add_media("n2", "b.jpg")
    monkeypatch.setattr(
        cli,
        "build_client",
        lambda session, concurrency: AmazonPhotosClient(
            session, transport=fake.transport, backoff_base=0.0
        ),
    )

    result = runner.invoke(cli.app, ["sync", "--dest", str(tmp_path / "lib")])
    assert result.exit_code == 0, result.output
    text = out.getvalue()
    for field in ("downloaded=2", "skipped=", "moved=", "archived=", "failed=0"):
        assert field in text


def test_failed_sync_exits_nonzero(tmp_path, monkeypatch, captured_console):
    credentials = tmp_path / "credentials.json"
    auth.save_session(COOKIES, path=credentials)
    monkeypatch.setenv("AMZ_DOWNLOAD_CREDENTIALS", str(credentials))
    fake = FakeAmazon()
    fake.add_media("bad", "bad.jpg", data=b"x", md5="deadbeef")
    monkeypatch.setattr(
        cli,
        "build_client",
        lambda session, concurrency: AmazonPhotosClient(
            session, transport=fake.transport, backoff_base=0.0
        ),
    )
    result = runner.invoke(cli.app, ["sync", "--dest", str(tmp_path / "lib")])
    assert result.exit_code == 1


# --- 7.4 status from recorded state only -------------------------------------


def test_status_reports_recorded_state_without_network(
    tmp_path, monkeypatch, captured_console
):
    out, _ = captured_console
    dest = tmp_path / "lib"
    store = StateStore(default_state_path(dest))
    store.initialize()
    node = Node(
        node_id="n1",
        name="a.jpg",
        media_type=MediaType.PHOTO,
        content_type="image/jpeg",
    )
    store.upsert_node(NodeRecord.from_node(node, canonical_path="2023/08/x_a.jpg"))
    node2 = Node(
        node_id="n2",
        name="b.jpg",
        media_type=MediaType.PHOTO,
        content_type="image/jpeg",
    )
    store.upsert_node(
        NodeRecord.from_node(
            node2, canonical_path="2023/08/x_b.jpg", status=NodeStatus.ARCHIVED
        )
    )
    store.finish_run(store.start_run(), downloaded=1)
    store.close()

    def forbidden(*args, **kwargs):
        raise AssertionError("status must not contact Amazon")

    monkeypatch.setattr(cli, "build_client", forbidden)
    result = runner.invoke(cli.app, ["status", "--dest", str(dest)])
    assert result.exit_code == 0
    text = out.getvalue()
    assert "present=1" in text
    assert "archived=1" in text
    assert "downloaded=1" in text


def test_status_on_missing_state_is_zero(tmp_path, captured_console):
    out, _ = captured_console
    result = runner.invoke(cli.app, ["status", "--dest", str(tmp_path / "empty")])
    assert result.exit_code == 0
    assert "present=0" in out.getvalue()


def test_login_rejects_missing_cookie(tmp_path, monkeypatch, captured_console):
    _, err = captured_console
    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text("amazon.com\tTRUE\t/\tTRUE\t1\tat_main\tsecret\n")
    monkeypatch.setenv("AMZ_DOWNLOAD_CREDENTIALS", str(tmp_path / "creds.json"))
    result = runner.invoke(cli.app, ["login", "--cookie-file", str(cookie_file)])
    assert result.exit_code == 2
    assert "ubid" in err.getvalue()
    assert "secret" not in err.getvalue()


# --- session check command ---------------------------------------------------


def _fake_rejecting_usage() -> FakeAmazon:
    fake = FakeAmazon()
    fake.on_request = lambda request, server: (
        httpx.Response(401, text="nope")
        if request.url.path.endswith("/account/usage")
        else None
    )
    return fake


def _patch_client(monkeypatch, fake: FakeAmazon) -> None:
    monkeypatch.setattr(
        cli,
        "build_client",
        lambda session, concurrency: AmazonPhotosClient(
            session, transport=fake.transport, backoff_base=0.0
        ),
    )


def test_check_reports_valid_session(tmp_path, monkeypatch, captured_console):
    out, _ = captured_console
    creds = tmp_path / "credentials.json"
    auth.save_session(COOKIES, path=creds)
    monkeypatch.setenv("AMZ_DOWNLOAD_CREDENTIALS", str(creds))
    _patch_client(monkeypatch, FakeAmazon())
    result = runner.invoke(cli.app, ["check"])
    assert result.exit_code == 0
    assert "session valid" in out.getvalue()


def test_check_without_session_is_nonzero(tmp_path, monkeypatch):
    monkeypatch.setenv("AMZ_DOWNLOAD_CREDENTIALS", str(tmp_path / "absent.json"))
    result = runner.invoke(cli.app, ["check"])
    assert result.exit_code == 2


def test_check_rejected_session_is_nonzero(tmp_path, monkeypatch, captured_console):
    _, err = captured_console
    creds = tmp_path / "credentials.json"
    auth.save_session(COOKIES, path=creds)
    monkeypatch.setenv("AMZ_DOWNLOAD_CREDENTIALS", str(creds))
    _patch_client(monkeypatch, _fake_rejecting_usage())
    result = runner.invoke(cli.app, ["check"])
    assert result.exit_code == 2
    assert "session invalid" in err.getvalue()
