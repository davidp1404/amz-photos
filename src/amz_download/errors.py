"""Exception types shared across amz_download."""

from __future__ import annotations


class AmzError(Exception):
    """Base class for all amz-download errors."""


class AuthError(AmzError):
    """Authentication could not be established or has expired."""


class MissingCookieError(AuthError):
    """One or more required cookies are absent from the supplied set."""

    def __init__(self, missing: list[str]) -> None:
        self.missing = list(missing)
        names = ", ".join(self.missing)
        super().__init__(f"Missing required Amazon cookies: {names}")


class NoCookiesFoundError(AuthError):
    """No usable Amazon cookies could be found in a Firefox profile."""


class SessionExpiredError(AuthError):
    """The stored session was rejected by Amazon Photos."""


class DownloadError(AmzError):
    """A media item could not be downloaded."""


class HashMismatchError(DownloadError):
    """Downloaded content did not match the expected content hash."""
