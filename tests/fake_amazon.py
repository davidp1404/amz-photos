"""A fake Amazon Photos ``drive/v1`` server backed by ``httpx.MockTransport``.

Reused by client, sync, and end-to-end tests. It implements just enough of the
undocumented API surface documented in ``spike-findings.md``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx

WORDS = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"


@dataclass
class FakeNode:
    node_id: str
    name: str
    content_type: str = "image/jpeg"
    content_date: str | None = "2023-08-14T12:00:00.000Z"
    parents: list[str] = field(default_factory=lambda: [])
    data: bytes = b""
    md5: str | None = None
    kind: str = "FILE"

    def payload(self) -> dict[str, Any]:
        if self.kind != "FILE":
            return {
                "id": self.node_id,
                "name": self.name,
                "kind": self.kind,
                "parents": self.parents,
            }
        digest = (
            self.md5 if self.md5 is not None else hashlib.md5(self.data).hexdigest()
        )
        props: dict[str, Any] = {
            "contentType": self.content_type,
            "size": len(self.data),
            "md5": digest,
        }
        if self.content_date is not None:
            props["contentDate"] = self.content_date
        return {
            "id": self.node_id,
            "name": self.name,
            "kind": "FILE",
            "parents": self.parents,
            "contentProperties": props,
        }


@dataclass
class FakeFolder:
    node_id: str
    name: str
    parents: list[str] = field(default_factory=list)

    def payload(self) -> dict[str, Any]:
        return {
            "id": self.node_id,
            "name": self.name,
            "kind": "FOLDER",
            "parents": self.parents,
        }


@dataclass
class FakeAlbum:
    album_id: str
    name: str
    members: list[str] = field(default_factory=list)

    def payload(self) -> dict[str, Any]:
        return {"id": self.album_id, "name": self.name, "kind": "VISUAL_COLLECTION"}


class FakeAmazon:
    """Configurable fake server. Mutate attributes between requests at will."""

    def __init__(
        self,
        *,
        owner_id: str = "owner-1",
        latency: float = 0.0,
        page_size: int = 200,
    ) -> None:
        self.owner_id = owner_id
        self.latency = latency
        self.page_size = page_size
        self.nodes: dict[str, FakeNode] = {}
        self.folders: dict[str, FakeFolder] = {}
        self.albums: dict[str, FakeAlbum] = {}
        # Fault injection: substring -> remaining number of 503 responses.
        self.faults: dict[str, int] = {}
        # Optional hook returning an httpx.Response to override handling.
        self.on_request: (
            Callable[[httpx.Request, "FakeAmazon"], httpx.Response | None] | None
        ) = None
        self.requests: list[tuple[str, str, dict[str, str]]] = []
        self.path_counts: dict[str, int] = {}
        self.active = 0
        self.max_active = 0
        self.transport = httpx.MockTransport(self.handle_async_request)

    # --- authoring helpers ---------------------------------------------------

    def add_media(
        self,
        node_id: str,
        name: str,
        *,
        data: bytes | None = None,
        content_type: str = "image/jpeg",
        content_date: str | None = "2023-08-14T12:00:00.000Z",
        parents: list[str] | None = None,
        md5: str | None = None,
    ) -> FakeNode:
        node = FakeNode(
            node_id=node_id,
            name=name,
            content_type=content_type,
            content_date=content_date,
            parents=list(parents or []),
            data=data if data is not None else f"content-{node_id}".encode(),
            md5=md5,
        )
        self.nodes[node_id] = node
        return node

    def add_folder(
        self, node_id: str, name: str, parents: list[str] | None = None
    ) -> FakeFolder:
        folder = FakeFolder(
            node_id=node_id,
            name=name,
            parents=list(parents) if parents is not None else [self.owner_id],
        )
        self.folders[node_id] = folder
        return folder

    def add_album(
        self, album_id: str, name: str, members: list[str] | None = None
    ) -> FakeAlbum:
        album = FakeAlbum(album_id=album_id, name=name, members=list(members or []))
        self.albums[album_id] = album
        return album

    def remove_media(self, node_id: str) -> None:
        self.nodes.pop(node_id, None)

    # --- handler -------------------------------------------------------------

    def _handle_sync(self, request: httpx.Request) -> httpx.Response:
        return self._route(request)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if self.latency:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            try:
                await asyncio.sleep(self.latency)
            finally:
                self.active -= 1
        return self._route(request)

    def _route(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        params = dict(request.url.params)
        self.requests.append((request.method, path, params))
        self.path_counts[path] = self.path_counts.get(path, 0) + 1

        for needle, remaining in list(self.faults.items()):
            if needle in path and remaining > 0:
                self.faults[needle] = remaining - 1
                return httpx.Response(503, text="transient")

        if self.on_request is not None:
            override = self.on_request(request, self)
            if override is not None:
                return override

        if path.endswith("/account/usage"):
            return self._json({"total": {"bytes": 0, "count": 0}})
        if path.endswith("/nodes") and params.get("filters") == "isRoot:true":
            return self._json(
                {
                    "count": 1,
                    "data": [
                        {
                            "id": self.owner_id,
                            "ownerId": self.owner_id,
                            "kind": "FOLDER",
                            "name": "",
                        }
                    ],
                }
            )
        if path.endswith("/search"):
            return self._search(params)
        if (
            path.endswith("/nodes")
            and params.get("filters") == "kind:VISUAL_COLLECTION"
        ):
            return self._paginate([a.payload() for a in self.albums.values()], params)
        match = re.search(r"/nodes/([^/]+)/children$", path)
        if match:
            node_id = match.group(1)
            if node_id in self.albums:
                payloads = [
                    self.nodes[m].payload()
                    for m in self.albums[node_id].members
                    if m in self.nodes
                ]
            else:
                payloads = [
                    f.payload() for f in self.folders.values() if node_id in f.parents
                ]
            return self._paginate(payloads, params)
        match = re.search(r"/nodes/([^/]+)/contentRedirection$", path)
        if match:
            node_id = match.group(1)
            node = self.nodes.get(node_id)
            if node is None:
                return httpx.Response(404, text="not found")
            return httpx.Response(
                200,
                content=node.data,
                headers={"content-disposition": f'attachment; filename="{node.name}"'},
            )
        return httpx.Response(404, json={"message": f"unhandled {path}"})

    def _search(self, params: dict[str, str]) -> httpx.Response:
        filters = params.get("filters", "")
        if filters == "kind:FOLDER":
            payloads = [f.payload() for f in self.folders.values()]
        else:
            payloads = [n.payload() for n in self.nodes.values()]
        return self._paginate(payloads, params)

    def _paginate(
        self, payloads: list[dict[str, Any]], params: dict[str, str]
    ) -> httpx.Response:
        limit = int(params.get("limit", self.page_size))
        offset = int(params.get("offset", 0))
        window = payloads[offset : offset + limit]
        return self._json({"count": len(payloads), "data": window})

    @staticmethod
    def _json(payload: dict[str, Any]) -> httpx.Response:
        return httpx.Response(
            200, text=json.dumps(payload), headers={"content-type": "application/json"}
        )
