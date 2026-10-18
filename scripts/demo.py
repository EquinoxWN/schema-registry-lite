"""Demo: start the registry on a free loopback port, evolve a schema through the HTTP API, stop it.

usage: python scripts/demo.py   (prints each request and response; uses a temporary database)
"""

from __future__ import annotations

import json
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from schema_registry_lite.server import serve
from schema_registry_lite.store import Store


def user(*fields: dict[str, Any]) -> str:
    """Schema text of the shop.User record."""
    return json.dumps({"type": "record", "name": "User", "namespace": "shop", "fields": list(fields)})


def main() -> None:
    """Run the scenario and print a RESULT line."""
    with tempfile.TemporaryDirectory(dir=".tmp") as tmp:
        server = serve(Store(Path(tmp) / "registry.db"), "127.0.0.1", 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        statuses: list[int] = []

        def call(method: str, path: str, body: Any = None, note: str = "") -> None:
            data = json.dumps(body).encode() if body is not None else None
            req = urllib.request.Request(
                base + path,
                data=data,
                method=method,
                headers={"Content-Type": "application/vnd.schemaregistry.v1+json"},
            )
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    status, payload = resp.status, json.loads(resp.read())
            except urllib.error.HTTPError as e:
                with e:
                    status, payload = e.code, json.loads(e.read())
            statuses.append(status)
            print(f"{method} {path}{'   # ' + note if note else ''}")
            print(f"  -> {status} {json.dumps(payload)}")

        idf, email = {"name": "id", "type": "long"}, {"name": "email", "type": "string"}
        tier = {"name": "tier", "type": "string", "default": "free"}
        call("POST", "/subjects/users-value/versions", {"schema": user(idf, email)}, "v1")
        call(
            "POST", "/subjects/users-value/versions", {"schema": user(idf, email, tier)}, "v2: new field with a default"
        )
        call("POST", "/subjects/users-value/versions", {"schema": user(idf, email, tier)}, "same schema again")
        call(
            "POST",
            "/subjects/users-value/versions",
            {"schema": user(idf, email, tier, {"name": "age", "type": "int"})},
            "new required field: old events have no age",
        )
        call(
            "POST",
            "/subjects/users-value/versions",
            {"schema": user(idf, {"name": "email", "type": "bytes"}, tier)},
            "email string -> bytes",
        )
        call(
            "POST",
            "/compatibility/subjects/users-value/versions/latest",
            {"schema": user({"name": "id", "type": "int"}, email, tier)},
            "id long -> int (preview)",
        )
        call("PUT", "/config/users-value", {"compatibility": "FULL_TRANSITIVE"})
        call(
            "POST",
            "/subjects/users-value/versions",
            {"schema": user(idf, {"name": "email", "type": "bytes"}, {"name": "tier", "type": "string"})},
            "tier without a default: fine against v3, breaks v1",
        )
        call("GET", "/subjects/users-value/versions")
        call("GET", "/schemas/ids/2")
        server.shutdown()
        server.server_close()
    accepted, rejected = statuses.count(200), statuses.count(409)
    print(f"RESULT demo: {len(statuses)} requests, {accepted} answered 200, {rejected} rejected as incompatible (409)")


if __name__ == "__main__":
    main()
