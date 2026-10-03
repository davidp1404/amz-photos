"""Task 2.6 — the authentication commands documented in README.md run as written."""

from __future__ import annotations

import io

import pytest
from rich.console import Console
from typer.testing import CliRunner

from amz_download import cli

runner = CliRunner()

COOKIE_FILE = """# Netscape HTTP Cookie File
.amazon.com\tTRUE\t/\tTRUE\t1999999999\tat_main\tAtza|doc
.amazon.com\tTRUE\t/\tTRUE\t1999999999\tubid_main\t131-doc
.amazon.com\tTRUE\t/\tTRUE\t1999999999\tsession-id\t999-doc
"""


@pytest.fixture
def consoles(monkeypatch):
    out, err = io.StringIO(), io.StringIO()
    monkeypatch.setattr(cli, "console", Console(file=out, width=200))
    monkeypatch.setattr(cli, "err_console", Console(file=err, width=200))
    return out, err


def test_documented_cookie_file_login(tmp_path, consoles, monkeypatch):
    monkeypatch.setenv("AMZ_DOWNLOAD_CREDENTIALS", str(tmp_path / "credentials.json"))
    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text(COOKIE_FILE)
    result = runner.invoke(cli.app, ["login", "--cookie-file", str(cookie_file)])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "credentials.json").exists()


def test_documented_firefox_login_falls_back_cleanly(tmp_path, consoles, monkeypatch):
    monkeypatch.setenv("AMZ_DOWNLOAD_CREDENTIALS", str(tmp_path / "credentials.json"))
    monkeypatch.setenv("AMZ_DOWNLOAD_FIREFOX_DIR", str(tmp_path / "no-profile"))
    _, err = consoles
    result = runner.invoke(cli.app, ["login", "--firefox"])
    assert result.exit_code == 2
    assert "cookie-file" in err.getvalue()


def test_documented_status_and_views(tmp_path, consoles):
    result = runner.invoke(cli.app, ["status", "--dest", str(tmp_path / "lib")])
    assert result.exit_code == 0
    result = runner.invoke(cli.app, ["views", "--dest", str(tmp_path / "lib")])
    assert result.exit_code == 3  # no recorded state yet, as documented
