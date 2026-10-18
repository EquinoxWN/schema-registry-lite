"""HTTP API: the subset of the Confluent Schema Registry REST API that producers and consumers use."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import unquote, urlsplit

from .avro import SchemaError
from .compat import Mode
from .store import IncompatibleError, NotFoundError, Store

MAX_BODY = 1024 * 1024
CONTENT_TYPE = "application/vnd.schemaregistry.v1+json"

Handler = Callable[[Store, dict[str, Any], list[str]], tuple[int, Any]]


class ApiError(Exception):
    """An error response in the Confluent format."""

    def __init__(self, status: int, code: int, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def _schema_text(body: dict[str, Any]) -> str:
    """The schema string from a request body; only Avro is supported in M1."""
    kind = body.get("schemaType", "AVRO")
    if kind != "AVRO":
        raise ApiError(422, 42201, f"schemaType {str(kind)[:20]!r} is not supported (M1 supports AVRO)")
    if body.get("references"):
        raise ApiError(422, 42201, "schema references are not supported")
    text = body.get("schema")
    if not isinstance(text, str):
        raise ApiError(422, 42201, "request body needs a 'schema' string")
    return text


def subjects(store: Store, _: dict[str, Any], __: list[str]) -> tuple[int, Any]:
    """GET /subjects"""
    return 200, store.subjects()


def versions(store: Store, _: dict[str, Any], args: list[str]) -> tuple[int, Any]:
    """GET /subjects/{subject}/versions"""
    return 200, store.versions(args[0])


def version(store: Store, _: dict[str, Any], args: list[str]) -> tuple[int, Any]:
    """GET /subjects/{subject}/versions/{version|latest}"""
    v = store.version(args[0], args[1])
    return 200, {"subject": v.subject, "version": v.version, "id": v.id, "schema": v.schema}


def register(store: Store, body: dict[str, Any], args: list[str]) -> tuple[int, Any]:
    """POST /subjects/{subject}/versions -> {"id": n}"""
    return 200, {"id": store.register(args[0], _schema_text(body)).id}


def lookup(store: Store, body: dict[str, Any], args: list[str]) -> tuple[int, Any]:
    """POST /subjects/{subject}: is this schema registered under the subject?"""
    v = store.lookup(args[0], _schema_text(body))
    return 200, {"subject": v.subject, "version": v.version, "id": v.id, "schema": v.schema}


def schema_by_id(store: Store, _: dict[str, Any], args: list[str]) -> tuple[int, Any]:
    """GET /schemas/ids/{id}"""
    return 200, {"schema": store.schema(int(args[0]))}


def compatibility(store: Store, body: dict[str, Any], args: list[str]) -> tuple[int, Any]:
    """POST /compatibility/subjects/{subject}/versions/latest -> is_compatible and the reasons."""
    problems = store.compatibility(args[0], _schema_text(body))
    return 200, {"is_compatible": not problems, "messages": problems}


def get_config(store: Store, _: dict[str, Any], args: list[str]) -> tuple[int, Any]:
    """GET /config and /config/{subject}"""
    return 200, {"compatibilityLevel": store.mode(args[0] if args else None).value}


def put_config(store: Store, body: dict[str, Any], args: list[str]) -> tuple[int, Any]:
    """PUT /config and /config/{subject} with {"compatibility": MODE}"""
    try:
        mode = Mode(str(body.get("compatibility")))
    except ValueError:
        raise ApiError(422, 42203, f"compatibility must be one of {[m.value for m in Mode]}") from None
    store.set_mode(args[0] if args else None, mode)
    return 200, {"compatibility": mode.value}


SEGMENT = r"([^/]+)"
ROUTES: list[tuple[str, re.Pattern[str], Handler]] = [
    ("GET", re.compile(r"^/subjects/?$"), subjects),
    ("GET", re.compile(rf"^/subjects/{SEGMENT}/versions/?$"), versions),
    ("GET", re.compile(rf"^/subjects/{SEGMENT}/versions/{SEGMENT}$"), version),
    ("POST", re.compile(rf"^/subjects/{SEGMENT}/versions/?$"), register),
    ("POST", re.compile(rf"^/subjects/{SEGMENT}/?$"), lookup),
    ("GET", re.compile(r"^/schemas/ids/([0-9]{1,18})$"), schema_by_id),
    ("POST", re.compile(rf"^/compatibility/subjects/{SEGMENT}/versions/latest$"), compatibility),
    ("GET", re.compile(r"^/config/?$"), get_config),
    ("GET", re.compile(rf"^/config/{SEGMENT}$"), get_config),
    ("PUT", re.compile(r"^/config/?$"), put_config),
    ("PUT", re.compile(rf"^/config/{SEGMENT}$"), put_config),
]


def make_handler(store: Store) -> type[BaseHTTPRequestHandler]:
    """A request handler class bound to one store."""

    class RegistryHandler(BaseHTTPRequestHandler):
        """Dispatches requests to the route functions and maps errors to Confluent error codes."""

        server_version = "schema-registry-lite/0.1"
        sys_version = ""

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
            """Silence the default per-request log line."""

        def _send(self, status: int, payload: Any) -> None:
            """Write a JSON response."""
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", CONTENT_TYPE)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        def _body(self) -> dict[str, Any]:
            """Read a bounded JSON object body."""
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                self.close_connection = True  # the unread body must not be parsed as the next request
                raise ApiError(413, 41301, f"request body larger than {MAX_BODY} bytes")
            if length == 0:
                return {}
            try:
                body = json.loads(self.rfile.read(length))
            except (json.JSONDecodeError, UnicodeDecodeError):
                raise ApiError(400, 40001, "request body is not JSON") from None
            if not isinstance(body, dict):
                raise ApiError(400, 40001, "request body must be a JSON object")
            return body

        def _dispatch(self, method: str) -> None:
            """Find the route, run it, translate exceptions."""
            path = urlsplit(self.path).path
            try:
                for m, pattern, fn in ROUTES:
                    match = pattern.match(path)
                    if match and m == method:
                        body = self._body() if method in ("POST", "PUT") else {}
                        status, payload = fn(store, body, [unquote(g) for g in match.groups()])
                        self._send(status, payload)
                        return
                if any(p.match(path) for _, p, _ in ROUTES):
                    raise ApiError(405, 405, f"method {method} not allowed on {path}")
                raise ApiError(404, 404, f"no route for {path[:80]}")
            except ApiError as e:
                self._send(e.status, {"error_code": e.code, "message": e.message})
            except IncompatibleError as e:
                self._send(409, {"error_code": 409, "message": "schema is incompatible", "messages": e.problems})
            except SchemaError as e:
                self._send(422, {"error_code": 42201, "message": f"invalid Avro schema: {e}"})
            except NotFoundError as e:
                text = str(e)
                code = 40403 if text.startswith("schema") else 40402 if text.startswith("version") else 40401
                self._send(404, {"error_code": code, "message": text})
            except ValueError as e:
                self._send(422, {"error_code": 42202, "message": str(e)})
            except Exception:  # never leak a traceback to the client
                self._send(500, {"error_code": 50001, "message": "internal error"})

        def do_GET(self) -> None:
            """Handle GET."""
            self._dispatch("GET")

        def do_POST(self) -> None:
            """Handle POST."""
            self._dispatch("POST")

        def do_PUT(self) -> None:
            """Handle PUT."""
            self._dispatch("PUT")

        def do_DELETE(self) -> None:
            """DELETE is not part of M1."""
            self._dispatch("DELETE")

    return RegistryHandler


def serve(store: Store, host: str, port: int) -> ThreadingHTTPServer:
    """Create (not start) a threaded HTTP server on host:port."""
    server = ThreadingHTTPServer((host, port), make_handler(store))
    server.daemon_threads = True
    return server
