"""Task 8.1 — end-to-end integration test against a fake Amazon server.

Covers login, first sync with albums and tree, view generation, a re-sync with a
move and a remote deletion, state rebuild (repair) without re-downloading, and a
no-op run.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from rich.console import Console
from typer.testing import CliRunner

from amz_download import cli
from amz_download.client import AmazonPhotosClient
from amz_download.models import NodeStatus
from amz_download.state import StateStore, default_state_path
from fake_amazon import FakeAmazon

COOKIE_FILE = """# Netscape HTTP Cookie File
.amazon.com\tTRUE\t/\tTRUE\t1999999999\tat_main\tAtza|integration
.amazon.com\tTRUE\t/\tTRUE\t1999999999\tubid_main\t131-integration
.amazon.com\tTRUE\t/\tTRUE\t1999999999\tsession-id\t999-integration
"""

runner = CliRunner()


def content_requests(fake: FakeAmazon) -> int:
    return sum(
        count
        for path, count in fake.path_counts.items()
        if path.endswith("/contentRedirection")
    )


@pytest.fixture
def environment(tmp_path, monkeypatch):
    fake = FakeAmazon()
    fake.add_folder("f1", "Pictures")
    fake.add_media("n1", "one.jpg", data=b"one-bytes", parents=["f1"])
    fake.add_media("n2", "two.jpg", data=b"two-bytes", parents=["f1"])
    fake.add_album("al1", "Summer", members=["n1", "n2"])

    creds = tmp_path / "credentials.json"
    monkeypatch.setenv("AMZ_DOWNLOAD_CREDENTIALS", str(creds))
    monkeypatch.setattr(
        cli,
        "build_client",
        lambda session, concurrency: AmazonPhotosClient(
            session, transport=fake.transport, backoff_base=0.0
        ),
    )
    # Deterministic console output capture.
    buffer = io.StringIO()
    monkeypatch.setattr(cli, "console", Console(file=buffer, width=200))
    monkeypatch.setattr(cli, "err_console", Console(file=buffer, width=200))
    return fake, creds, tmp_path / "lib", buffer


def test_end_to_end(tmp_path, environment):
    fake, creds, dest, output = environment

    # --- login (through the CLI) --------------------------------------------
    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text(COOKIE_FILE)
    login = runner.invoke(cli.app, ["login", "--cookie-file", str(cookie_file)])
    assert login.exit_code == 0, login.output
    assert creds.exists()

    # --- first sync with albums and tree ------------------------------------
    first = runner.invoke(cli.app, ["sync", "--dest", str(dest)])
    assert first.exit_code == 0, first.output

    n1 = dest / "2023" / "08" / "2023-08-14_one.jpg"
    n2 = dest / "2023" / "08" / "2023-08-14_two.jpg"
    assert n1.read_bytes() == b"one-bytes"
    assert n2.read_bytes() == b"two-bytes"
    assert (dest / "_by-tree" / "Pictures" / "2023-08-14_one.jpg").is_symlink()
    assert (dest / "_by-album" / "Summer" / "2023-08-14_two.jpg").is_symlink()

    # --- re-sync with a move and a remote deletion --------------------------
    fake.nodes["n1"].name = "renamed.jpg"  # move (same node id)
    fake.remove_media("n2")  # remote deletion
    requests_before = content_requests(fake)
    second = runner.invoke(cli.app, ["sync", "--dest", str(dest)])
    assert second.exit_code == 0, second.output

    renamed = dest / "2023" / "08" / "2023-08-14_renamed.jpg"
    assert renamed.exists() and not n1.exists()  # relocated, not re-downloaded
    assert n2.exists()  # archived locally, retained
    assert content_requests(fake) == requests_before

    store = StateStore(default_state_path(dest))
    assert store.get_node("n2").status == NodeStatus.ARCHIVED.value
    store.close()
    # Old album/tree links for the moved node are regenerated.
    assert (dest / "_by-tree" / "Pictures" / "2023-08-14_renamed.jpg").is_symlink()

    # --- state rebuild (repair) without re-downloading ----------------------
    default_state_path(dest).unlink()  # simulate lost state
    requests_before_repair = content_requests(fake)
    repair = runner.invoke(cli.app, ["repair", "--dest", str(dest)])
    assert repair.exit_code == 0, repair.output
    assert "adopted=" in output.getvalue()
    # Unmatched remote-deleted file is retained, not deleted.
    assert n2.exists()

    after_repair = runner.invoke(cli.app, ["sync", "--dest", str(dest)])
    assert after_repair.exit_code == 0, after_repair.output
    assert content_requests(fake) == requests_before_repair  # no re-download

    # --- no-op run ----------------------------------------------------------
    requests_before_noop = content_requests(fake)
    noop = runner.invoke(cli.app, ["sync", "--dest", str(dest)])
    assert noop.exit_code == 0, noop.output
    assert content_requests(fake) == requests_before_noop
