"""Shared helpers: schema builders and a running registry server."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from schema_registry_lite.server import serve
from schema_registry_lite.store import Store

Call = Callable[..., tuple[int, Any]]


def record(*fields: dict[str, Any], name: str = "User", namespace: str = "shop") -> str:
    """Schema text of a record with the given field objects."""
    return json.dumps({"type": "record", "name": name, "namespace": namespace, "fields": list(fields)})


def fld(name: str, type_: Any, **extra: Any) -> dict[str, Any]:
    """One field object; pass default=... to give it a default."""
    return {"name": name, "type": type_, **extra}


@pytest.fixture
def store(tmp_path: Path) -> Store:
    """A fresh registry in a temporary SQLite file."""
    return Store(tmp_path / "registry.db")


@pytest.fixture
def api(store: Store) -> Iterator[Call]:
    """A real HTTP server on 127.0.0.1 and a call(method, path, body) helper."""
    server = serve(store, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def call(method: str, path: str, body: Any = None, raw: bytes | None = None, port: bool = False) -> tuple[int, Any]:
        if port:  # lets a test open its own low-level connection
            return 0, server.server_address[1]
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(
            base + path, data=data, method=method, headers={"Content-Type": "application/vnd.schemaregistry.v1+json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.loads(e.read())

    yield call
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)
