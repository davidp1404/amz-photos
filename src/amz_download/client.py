"""Thin async Amazon Photos ``drive/v1`` client.

Design D1: all endpoint knowledge lives here so undocumented-endpoint breakage is
localised. The client is strictly read-only. Concurrency, retry/backoff, and
request spacing are bounded to reduce throttling risk.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import random
from pathlib import Path
from typing import Any, AsyncIterator, Iterable, Mapping

import httpx

from .errors import AmzError, DownloadError, HashMismatchError, SessionExpiredError
from .models import (
    Album,
    Folder,
    Node,
    is_media_node,
    node_id_of,
    parse_album,
    parse_folder,
    parse_node,
)

DEFAULT_TLD = "com"
DEFAULT_PAGE_SIZE = 200
MEDIA_FILTER = "type:(PHOTOS OR VIDEOS)"
FOLDER_FILTER = "kind:FOLDER"
ALBUM_FILTER = "kind:VISUAL_COLLECTION"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
# Amazon's /search endpoint rejects requests without searchContext (400).
SEARCH_EXTRA = {
    "lowResThumbnail": "true",
    "searchContext": "customer",
    "sort": "['createdDate DESC']",
}
RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})
DOWNLOAD_CHUNK = 256 * 1024


def determine_tld(cookies: Mapping[str, Any]) -> str:
    """Derive the Amazon TLD from cookie names (design D6)."""
    for name in cookies:
        if name.endswith("_main"):
            return "com"
        if name.startswith("at-acb"):
            return name[len("at-acb") :]
    return DEFAULT_TLD


class RateLimiter:
    """Enforce a minimum interval between request starts."""

    def __init__(self, min_interval: float = 0.0) -> None:
        self.min_interval = float(min_interval)
        self._next_allowed = 0.0

    async def acquire(self) -> None:
        if self.min_interval <= 0:
            return
        loop = asyncio.get_running_loop()
        now = loop.time()
        wait = self._next_allowed - now
        if wait > 0:
            await asyncio.sleep(wait)
        self._next_allowed = max(self._next_allowed, loop.time()) + self.min_interval


class AmazonPhotosClient:
    """Read-only client for the undocumented Amazon Photos web API."""

    def __init__(
        self,
        cookies: Mapping[str, Any],
        *,
        tld: str | None = None,
        concurrency: int = 4,
        min_request_interval: float = 0.0,
        max_retries: int = 6,
        backoff_base: float = 1.5,
        backoff_cap: float = 20.0,
        timeout: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> None:
        if concurrency < 1:
            raise ValueError("concurrency must be >= 1")
        self.cookies = {str(k): str(v) for k, v in cookies.items()}
        self.tld = tld or determine_tld(self.cookies)
        self.base_url = f"https://www.amazon.{self.tld}/drive/v1"
        self.concurrency = concurrency
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.backoff_cap = backoff_cap
        self.page_size = page_size
        self._semaphore = asyncio.Semaphore(concurrency)
        self._limiter = RateLimiter(min_request_interval)
        self._root: dict[str, Any] | None = None
        self._client = httpx.AsyncClient(
            transport=transport,
            timeout=timeout,
            follow_redirects=True,
            headers={
                "user-agent": USER_AGENT,
                "x-amzn-sessionid": self.cookies.get("session-id", ""),
            },
            cookies=self.cookies,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "AmazonPhotosClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    # --- transport -----------------------------------------------------------

    def _base_params(self) -> dict[str, str]:
        return {
            "asset": "ALL",
            "tempLink": "false",
            "resourceVersion": "V2",
            "ContentType": "JSON",
        }

    async def _request(
        self, method: str, url: str, *, params: Mapping[str, Any] | None = None
    ) -> httpx.Response:
        last_error: Exception | None = None
        attempt = 0
        while True:
            attempt += 1
            await self._limiter.acquire()
            try:
                async with self._semaphore:
                    response = await self._client.request(method, url, params=params)
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last_error = exc
                if attempt > self.max_retries:
                    raise AmzError(
                        f"Amazon request failed after retries: {exc}"
                    ) from exc
                await self._sleep_backoff(attempt)
                continue

            if response.status_code == 401:
                raise SessionExpiredError(
                    "Amazon rejected the stored session; re-authenticate with "
                    "`amz-download login`."
                )
            if response.status_code in RETRYABLE_STATUS:
                last_error = AmzError(
                    f"Amazon returned retryable status {response.status_code}"
                )
                if attempt > self.max_retries:
                    raise last_error
                await self._sleep_backoff(attempt)
                continue
            if response.status_code >= 400:
                raise AmzError(
                    f"Amazon request to {httpx.URL(url).path} failed with "
                    f"status {response.status_code}"
                )
            return response
        # pragma: no cover - loop always returns or raises
        raise last_error if last_error else AmzError("unreachable")

    async def _sleep_backoff(self, attempt: int) -> None:
        delay = min(self.backoff_base**attempt, self.backoff_cap)
        if delay <= 0:
            return
        await asyncio.sleep(delay)

    async def _get_json(
        self, path: str, *, params: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        response = await self._request("GET", f"{self.base_url}{path}", params=params)
        return response.json()

    # --- session -------------------------------------------------------------

    async def check_session(self) -> None:
        """Validate the session; raises :class:`SessionExpiredError` on 401."""
        await self._get_json("/account/usage", params=self._base_params())

    async def get_owner_id(self) -> str:
        root = await self._get_root()
        owner_id = str(root.get("ownerId") or root.get("id") or "")
        if not owner_id:
            raise AmzError("Amazon root node has no owner id")
        return owner_id

    async def _get_root(self) -> dict[str, Any]:
        if self._root is None:
            data = await self._get_json(
                "/nodes", params={"filters": "isRoot:true", **self._base_params()}
            )
            nodes = data.get("data") or []
            if not nodes:
                raise AmzError("Amazon returned no root node")
            self._root = nodes[0]
        return self._root

    # --- listing -------------------------------------------------------------

    async def _paginate(
        self,
        path: str,
        *,
        params: Mapping[str, Any],
        data_key: str = "data",
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        offset = 0
        total: int | None = None
        while True:
            page_params = dict(params)
            page_params.update({"limit": self.page_size, "offset": offset})
            data = await self._get_json(path, params=page_params)
            items = data.get(data_key) or []
            results.extend(items)
            if total is None:
                total = int(data.get("count") or len(items))
            offset += len(items)
            if not items or offset >= total:
                break
        return results

    async def list_media(self) -> list[Node]:
        """List every photo and video node, paginating to the end."""
        payloads = await self._paginate(
            "/search",
            params={**self._base_params(), **SEARCH_EXTRA, "filters": MEDIA_FILTER},
        )
        nodes: list[Node] = []
        for payload in payloads:
            if not is_media_node(payload):
                continue
            node = parse_node(payload)
            if node is not None:
                nodes.append(node)
        return nodes

    async def list_folders(self) -> list[Folder]:
        """List every folder by traversing ``/nodes/{id}/children?filters=kind:FOLDER``.

        Amazon's ``/search`` endpoint rejects a ``kind`` filter
        (``Invalid fieldName: kind``), so folders are discovered from the root
        down, as in the reference implementation.
        """
        root = await self._get_root()
        root_id = str(root.get("id") or "")
        if not root_id:
            raise AmzError("Amazon root node has no id")
        folders: list[Folder] = []
        seen: set[str] = set()
        queue: list[str] = [root_id]
        while queue:
            parent_id = queue.pop(0)
            payloads = await self._paginate(
                f"/nodes/{parent_id}/children",
                params={**self._base_params(), "filters": FOLDER_FILTER},
            )
            for payload in payloads:
                folder = parse_folder(payload)
                if folder and folder.node_id not in seen:
                    seen.add(folder.node_id)
                    folders.append(folder)
                    queue.append(folder.node_id)
        return folders

    async def list_albums(self) -> list[Album]:
        """List every album (``VISUAL_COLLECTION`` node)."""
        payloads = await self._paginate(
            "/nodes", params={**self._base_params(), "filters": ALBUM_FILTER}
        )
        albums: list[Album] = []
        seen: set[str] = set()
        for payload in payloads:
            album = parse_album(payload)
            if album and album.album_id not in seen:
                seen.add(album.album_id)
                albums.append(album)
        return albums

    async def album_members(self, album_id: str) -> list[str]:
        """Return the media node ids belonging to an album."""
        payloads = await self._paginate(
            f"/nodes/{album_id}/children", params=self._base_params()
        )
        members: list[str] = []
        for payload in payloads:
            if not is_media_node(payload):
                continue
            node_id = node_id_of(payload)
            if node_id:
                members.append(node_id)
        return members

    # --- download ------------------------------------------------------------

    async def download(
        self,
        node_id: str,
        destination: str | os.PathLike[str],
        *,
        expected_md5: str | None = None,
        chunk_size: int = DOWNLOAD_CHUNK,
    ) -> str:
        """Stream a node's content to ``destination`` and return its md5.

        The caller chooses the destination (normally a temporary path) and is
        responsible for atomic finalisation. Raises :class:`HashMismatchError`
        when ``expected_md5`` is given and does not match.
        """
        owner_id = await self.get_owner_id()
        url = f"{self.base_url}/nodes/{node_id}/contentRedirection"
        params = {"download": "true", "ownerId": owner_id}
        target = Path(destination)
        digest = hashlib.md5()
        await self._limiter.acquire()
        try:
            async with self._semaphore:
                async with self._client.stream("GET", url, params=params) as response:
                    if response.status_code == 401:
                        raise SessionExpiredError(
                            "Amazon rejected the stored session during download; "
                            "re-authenticate with `amz-download login`."
                        )
                    if response.status_code >= 400:
                        raise DownloadError(
                            f"download of {node_id} failed with status "
                            f"{response.status_code}"
                        )
                    with open(target, "wb") as handle:
                        async for chunk in response.aiter_bytes(chunk_size):
                            if chunk:
                                handle.write(chunk)
                                digest.update(chunk)
        except httpx.HTTPError as exc:
            raise DownloadError(f"download of {node_id} failed: {exc}") from exc
        computed = digest.hexdigest()
        if expected_md5 and computed.lower() != expected_md5.lower():
            raise HashMismatchError(
                f"content hash mismatch for {node_id}: expected {expected_md5}, "
                f"got {computed}"
            )
        return computed


async def stream_iter(response: httpx.Response) -> AsyncIterator[bytes]:
    async for chunk in response.aiter_bytes():
        yield chunk
