"""Authentication: cookie parsing, Firefox extraction, credential storage.

Design D6: a manually supplied cookie file is the baseline; reading a local
Firefox profile is a convenience path (Linux cookie values are unencrypted, so
the standard library's ``sqlite3`` suffices). Regional cookie names are accepted.
No cookie value is ever written to logs or error messages.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any, Mapping

from .errors import MissingCookieError, NoCookiesFoundError, SessionExpiredError

# Cookie families. ``at`` and ``ubid`` have a default name and per-TLD regional
# variants (e.g. ``at-acbde`` / ``ubid-acbde``).
AT_DEFAULT = "at_main"
AT_PREFIX = "at"
UBID_DEFAULT = "ubid_main"
UBID_PREFIX = "ubid"
SESSION_COOKIE = "session-id"

REQUIRED_FAMILIES: tuple[tuple[str, str, str], ...] = (
    ("at", AT_DEFAULT, AT_PREFIX),
    ("ubid", UBID_DEFAULT, UBID_PREFIX),
    (SESSION_COOKIE, SESSION_COOKIE, SESSION_COOKIE),
)


def _family_present(cookies: Mapping[str, Any], default: str, prefix: str) -> bool:
    if default in cookies:
        return True
    if prefix == SESSION_COOKIE:
        return False
    return any(name == prefix or name.startswith(f"{prefix}-") for name in cookies)


def missing_required(cookies: Mapping[str, Any]) -> list[str]:
    """Return human-readable names of required cookie families that are absent."""
    missing: list[str] = []
    for label, default, prefix in REQUIRED_FAMILIES:
        if not _family_present(cookies, default, prefix):
            if default == label:
                missing.append(label)
            else:
                missing.append(f"{label} ({default} or {prefix}-<region>)")
    return missing


def validate_cookies(cookies: Mapping[str, Any]) -> None:
    """Raise :class:`MissingCookieError` if a required cookie family is absent.

    Never includes cookie values in the error.
    """
    missing = missing_required(cookies)
    if missing:
        raise MissingCookieError(missing)


def parse_cookie_file(text: str) -> dict[str, str]:
    """Parse cookie text in Netscape, ``name=value``, or JSON form.

    Accepts:
    - the Netscape ``cookies.txt`` tab-separated format,
    - one or more ``name=value`` pairs per line (optionally ``;``-separated),
    - a JSON object of ``name: value`` pairs, or a JSON list of
      ``{"name": ..., "value": ...}`` objects (as produced by cookie exporters).
    """
    cookies: dict[str, str] = {}
    stripped = text.strip()
    if not stripped:
        return cookies

    if stripped[0] in "{[":
        return _parse_json_cookies(stripped)

    for raw_line in stripped.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) >= 7:
            cookies[fields[5]] = fields[6]
            continue
        for pair in line.split(";"):
            pair = pair.strip()
            if "=" in pair:
                key, value = pair.split("=", 1)
                key = key.strip()
                if key:
                    cookies[key] = value.strip()
    return cookies


def _parse_json_cookies(text: str) -> dict[str, str]:
    data = json.loads(text)
    cookies: dict[str, str] = {}
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and item.get("name") is not None:
                cookies[str(item["name"])] = str(item.get("value", ""))
        return cookies
    if isinstance(data, dict):
        nested = data.get("cookies")
        if isinstance(nested, (list, dict)):
            return _parse_json_cookies(json.dumps(nested))
        for key, value in data.items():
            cookies[str(key)] = str(value)
    return cookies


def read_cookie_file(path: str | os.PathLike[str]) -> dict[str, str]:
    return parse_cookie_file(Path(path).read_text(encoding="utf-8"))


def firefox_profile_roots() -> list[Path]:
    """Candidate directories that contain Firefox profiles for this platform."""
    override = os.environ.get("AMZ_DOWNLOAD_FIREFOX_DIR")
    if override:
        return [Path(override).expanduser()]
    home = Path.home()
    if sys.platform == "darwin":
        return [home / "Library" / "Application Support" / "Firefox" / "Profiles"]
    if os.name == "nt":  # pragma: no cover - not exercised on Linux CI
        appdata = os.environ.get("APPDATA")
        if appdata:
            return [Path(appdata) / "Mozilla" / "Firefox" / "Profiles"]
        return []
    return [home / ".mozilla" / "firefox"]


def find_cookie_databases(profile: str | os.PathLike[str] | None = None) -> list[Path]:
    """Locate ``cookies.sqlite`` files, optionally for one explicit profile."""
    if profile is not None:
        root = Path(profile).expanduser()
        if root.is_file():
            return [root]
        candidate = root / "cookies.sqlite"
        return [candidate] if candidate.exists() else []
    found: list[Path] = []
    for root in firefox_profile_roots():
        if not root.exists():
            continue
        direct = root / "cookies.sqlite"
        if direct.exists():
            found.append(direct)
        found.extend(sorted(root.glob("*/cookies.sqlite")))
    return found


def extract_firefox_cookies(
    profile: str | os.PathLike[str] | None = None,
) -> dict[str, str]:
    """Read Amazon cookies from Firefox ``cookies.sqlite`` files.

    Returns a mapping of cookie name to value. Raises
    :class:`NoCookiesFoundError` when no database or no Amazon cookies are found.
    """
    databases = [p for p in find_cookie_databases(profile) if p.exists()]
    if not databases:
        raise NoCookiesFoundError(
            "No Firefox profile with a cookies database was found; "
            "supply cookies manually with --cookie-file."
        )
    cookies: dict[str, str] = {}
    for db_path in databases:
        try:
            for name, value in _read_amazon_cookies(db_path):
                cookies.setdefault(name, value)
        except sqlite3.Error:
            continue
    if not cookies:
        raise NoCookiesFoundError(
            "No Amazon cookies were found in the Firefox profile; "
            "sign in to Amazon Photos in Firefox or supply cookies manually "
            "with --cookie-file."
        )
    return cookies


def _read_amazon_cookies(db_path: Path) -> list[tuple[str, str]]:
    uri = f"file:{db_path}?mode=ro&immutable=1"
    con = sqlite3.connect(uri, uri=True)
    try:
        rows = con.execute(
            "SELECT name, value FROM moz_cookies WHERE host LIKE '%amazon%'"
        ).fetchall()
    finally:
        con.close()
    result: list[tuple[str, str]] = []
    for name, value in rows:
        if name:
            result.append((str(name), str(value)))
    return result


# --- credential storage ------------------------------------------------------


def credentials_path() -> Path:
    """Per-user credentials path, honouring ``XDG_CONFIG_HOME``."""
    override = os.environ.get("AMZ_DOWNLOAD_CREDENTIALS")
    if override:
        return Path(override).expanduser()
    config_home = os.environ.get("XDG_CONFIG_HOME")
    base = Path(config_home).expanduser() if config_home else Path.home() / ".config"
    return base / "amz-download" / "credentials.json"


def save_session(
    cookies: Mapping[str, Any], path: str | os.PathLike[str] | None = None
) -> Path:
    """Persist a session to an owner-only (``0600``) credentials file."""
    target = Path(path) if path is not None else credentials_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(target.parent, 0o700)
    except OSError:  # pragma: no cover - best effort on exotic filesystems
        pass
    payload = json.dumps({str(k): str(v) for k, v in cookies.items()}, indent=2)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
    finally:
        os.chmod(target, 0o600)
    return target


def load_session(path: str | os.PathLike[str] | None = None) -> dict[str, str] | None:
    """Load a stored session, or ``None`` when it is absent."""
    target = Path(path) if path is not None else credentials_path()
    if not target.exists():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return {str(k): str(v) for k, v in data.items()}


def redact(cookies: Mapping[str, Any]) -> dict[str, str]:
    """Return a copy with values replaced, for safe display."""
    return {str(k): "<redacted>" for k in cookies}


async def ensure_session(client: Any) -> None:
    """Validate the session held by ``client`` before doing any work.

    Propagates :class:`SessionExpiredError` raised by the client when Amazon
    rejects the session. Callers must invoke this before writing anything.
    """
    await client.check_session()


__all__ = [
    "MissingCookieError",
    "NoCookiesFoundError",
    "SessionExpiredError",
    "credentials_path",
    "ensure_session",
    "extract_firefox_cookies",
    "find_cookie_databases",
    "load_session",
    "missing_required",
    "parse_cookie_file",
    "read_cookie_file",
    "redact",
    "save_session",
    "validate_cookies",
]
