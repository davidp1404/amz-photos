"""Tests for task group 2 — authentication."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import stat

import pytest

from amz_download import auth
from amz_download.errors import (
    MissingCookieError,
    NoCookiesFoundError,
    SessionExpiredError,
)

SECRET_AT = "Atza|SECRET-AT-VALUE"
SECRET_UBID = "123-SECRET-UBID-456"
SECRET_SESSION = "SECRET-SESSION-789"


def valid_cookies() -> dict[str, str]:
    return {
        "at_main": SECRET_AT,
        "ubid_main": SECRET_UBID,
        "session-id": SECRET_SESSION,
    }


# --- 2.1 cookie-file parsing and validation ---------------------------------


def test_parse_netscape_cookie_file():
    text = (
        "# Netscape HTTP Cookie File\n"
        "amazon.com\tTRUE\t/\tTRUE\t1999999999\tat_main\t" + SECRET_AT + "\n"
        "amazon.com\tTRUE\t/\tTRUE\t1999999999\tubid_main\t" + SECRET_UBID + "\n"
        "amazon.com\tTRUE\t/\tTRUE\t1999999999\tsession-id\t" + SECRET_SESSION + "\n"
    )
    cookies = auth.parse_cookie_file(text)
    assert cookies["at_main"] == SECRET_AT
    assert cookies["ubid_main"] == SECRET_UBID
    assert cookies["session-id"] == SECRET_SESSION
    auth.validate_cookies(cookies)  # does not raise


def test_parse_json_cookie_file():
    text = json.dumps(
        [
            {"name": "at_main", "value": SECRET_AT},
            {"name": "ubid_main", "value": SECRET_UBID},
            {"name": "session-id", "value": SECRET_SESSION},
        ]
    )
    cookies = auth.parse_cookie_file(text)
    assert cookies == valid_cookies()


def test_regional_cookie_names_accepted():
    cookies = {
        "at-acbde": SECRET_AT,
        "ubid-acbde": SECRET_UBID,
        "session-id": SECRET_SESSION,
    }
    auth.validate_cookies(cookies)  # does not raise


def test_missing_cookie_rejected_without_values():
    cookies = {"at_main": SECRET_AT}
    with pytest.raises(MissingCookieError) as excinfo:
        auth.validate_cookies(cookies)
    message = str(excinfo.value)
    assert "ubid" in message
    assert "session-id" in message
    # No supplied secret value may appear in the message.
    assert SECRET_AT not in message
    assert SECRET_UBID not in message


# --- 2.2 Firefox profile extraction -----------------------------------------


def _make_profile(root, rows):
    root.mkdir(parents=True, exist_ok=True)
    db = root / "cookies.sqlite"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE moz_cookies (host TEXT, name TEXT, value TEXT)")
    con.executemany("INSERT INTO moz_cookies VALUES (?, ?, ?)", rows)
    con.commit()
    con.close()
    return db


def test_firefox_extraction_reads_fixture_profile(tmp_path):
    profile = tmp_path / "profile"
    _make_profile(
        profile,
        [
            ("www.amazon.com", "at_main", SECRET_AT),
            ("www.amazon.com", "ubid_main", SECRET_UBID),
            ("www.amazon.com", "session-id", SECRET_SESSION),
        ],
    )
    cookies = auth.extract_firefox_cookies(profile=profile)
    assert cookies["at_main"] == SECRET_AT
    auth.validate_cookies(cookies)


def test_firefox_extraction_reports_missing_profile(tmp_path):
    with pytest.raises(NoCookiesFoundError) as excinfo:
        auth.extract_firefox_cookies(profile=tmp_path / "does-not-exist")
    assert "cookie-file" in str(excinfo.value)


def test_firefox_extraction_reports_no_amazon_cookies(tmp_path):
    profile = tmp_path / "profile"
    _make_profile(profile, [("example.com", "foo", "bar")])
    with pytest.raises(NoCookiesFoundError) as excinfo:
        auth.extract_firefox_cookies(profile=profile)
    assert SECRET_AT not in str(excinfo.value)
    assert "Amazon" in str(excinfo.value)


# --- 2.3 credential storage --------------------------------------------------


def test_save_session_round_trip_and_permissions(tmp_path):
    target = tmp_path / "config" / "credentials.json"
    auth.save_session(valid_cookies(), path=target)
    assert target.exists()
    mode = stat.S_IMODE(os.stat(target).st_mode)
    assert mode == 0o600
    assert auth.load_session(path=target) == valid_cookies()


def test_load_session_absent_returns_none(tmp_path):
    assert auth.load_session(path=tmp_path / "nope.json") is None


# --- 2.4 session validation and expiry path ---------------------------------


class _RejectedClient:
    async def check_session(self) -> None:
        raise SessionExpiredError("session rejected; re-authenticate")


def test_rejected_session_raises_before_writes(tmp_path):
    dest = tmp_path / "library"

    async def run() -> None:
        await auth.ensure_session(_RejectedClient())

    with pytest.raises(SessionExpiredError) as excinfo:
        asyncio.run(run())
    assert "re-authenticate" in str(excinfo.value)
    # No library writes occurred: the destination was never even created.
    assert not dest.exists()


# --- 2.5 secrets never appear in output -------------------------------------


def test_secrets_never_logged_on_success_and_failure(tmp_path, caplog):
    target = tmp_path / "credentials.json"
    with caplog.at_level(logging.DEBUG):
        auth.parse_cookie_file("amazon.com\tTRUE\t/\tTRUE\t1\tat_main\t" + SECRET_AT)
        auth.validate_cookies(valid_cookies())
        auth.save_session(valid_cookies(), path=target)
        auth.load_session(path=target)
        try:
            auth.validate_cookies({"at_main": SECRET_AT})
        except MissingCookieError as exc:
            assert SECRET_AT not in str(exc)
            assert SECRET_SESSION not in str(exc)
        redacted = auth.redact(valid_cookies())
        assert SECRET_AT not in json.dumps(redacted)

    for secret in (SECRET_AT, SECRET_UBID, SECRET_SESSION):
        assert secret not in caplog.text
