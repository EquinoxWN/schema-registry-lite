"""The HTTP API end to end over a real loopback socket, with Confluent-style responses."""

from __future__ import annotations

import argparse
import http.client
import json

import pytest
from conftest import Call, fld, record

from schema_registry_lite.cli import loopback

V1 = record(fld("id", "long"))
V2 = record(fld("id", "long"), fld("tier", "string", default="free"))
BAD = record(fld("id", "long"), fld("age", "int"))


def test_register_read_and_list(api: Call) -> None:
    assert api("POST", "/subjects/users-value/versions", {"schema": V1}) == (200, {"id": 1})
    assert api("POST", "/subjects/users-value/versions", {"schema": V2}) == (200, {"id": 2})
    assert api("POST", "/subjects/users-value/versions", {"schema": V2}) == (200, {"id": 2})  # idempotent
    assert api("GET", "/subjects") == (200, ["users-value"])
    assert api("GET", "/subjects/users-value/versions") == (200, [1, 2])
    status, body = api("GET", "/subjects/users-value/versions/latest")
    assert (status, body["version"], body["id"]) == (200, 2, 2)
    assert api("GET", "/schemas/ids/1") == (200, {"schema": V1})
    status, body = api("POST", "/subjects/users-value", {"schema": V1})
    assert (status, body["version"]) == (200, 1)


def test_incompatible_schema_gets_409_with_reasons(api: Call) -> None:
    api("POST", "/subjects/s/versions", {"schema": V1})
    status, body = api("POST", "/subjects/s/versions", {"schema": BAD})
    assert status == 409
    assert body["error_code"] == 409
    assert body["messages"] == [
        "backward vs latest: $.age: reader field has no default and the writer does not have it"
    ]


def test_compatibility_endpoint_and_config(api: Call) -> None:
    api("POST", "/subjects/s/versions", {"schema": V1})
    assert api("POST", "/compatibility/subjects/s/versions/latest", {"schema": V2}) == (
        200,
        {"is_compatible": True, "messages": []},
    )
    status, body = api("POST", "/compatibility/subjects/s/versions/latest", {"schema": BAD})
    assert status == 200
    assert body["is_compatible"] is False
    assert api("GET", "/config") == (200, {"compatibilityLevel": "BACKWARD"})
    assert api("PUT", "/config/s", {"compatibility": "FORWARD"}) == (200, {"compatibility": "FORWARD"})
    assert api("GET", "/config/s") == (200, {"compatibilityLevel": "FORWARD"})
    assert (
        api("POST", "/subjects/s/versions", {"schema": BAD})[0] == 200
    )  # adding a required field is forward-compatible


@pytest.mark.parametrize(
    ("method", "path", "body", "status", "code"),
    [
        ("GET", "/subjects/none/versions", None, 404, 40401),
        ("GET", "/schemas/ids/42", None, 404, 40403),
        ("POST", "/subjects/s/versions", {"schema": "{bad"}, 422, 42201),
        ("POST", "/subjects/s/versions", {"schema": V1, "schemaType": "PROTOBUF"}, 422, 42201),
        ("POST", "/subjects/s/versions", {"nope": 1}, 422, 42201),
        ("PUT", "/config", {"compatibility": "SOMETIMES"}, 422, 42203),
        ("POST", "/subjects/bad%20name/versions", {"schema": V1}, 422, 42202),
        ("GET", "/nothing", None, 404, 404),
        ("DELETE", "/subjects", None, 405, 405),
    ],
)
def test_errors_use_confluent_codes(api: Call, method: str, path: str, body: object, status: int, code: int) -> None:
    got_status, got = api(method, path, body)
    assert (got_status, got["error_code"]) == (status, code), got


def test_version_not_found_and_bad_body(api: Call) -> None:
    api("POST", "/subjects/s/versions", {"schema": V1})
    assert api("GET", "/subjects/s/versions/7")[1]["error_code"] == 40402
    assert api("POST", "/subjects/s/versions", raw=b"not json")[0] == 400
    assert api("POST", "/subjects/s/versions", raw=b"[1, 2]")[0] == 400


def test_oversized_body_is_refused_before_it_is_read(api: Call) -> None:
    port = int(api("GET", "/config", port=True)[1])
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.putrequest("POST", "/subjects/s/versions")
    conn.putheader("Content-Length", str(50 * 1024 * 1024))  # claims 50 MiB, sends nothing
    conn.endheaders()
    resp = conn.getresponse()
    assert (resp.status, json.loads(resp.read())["error_code"]) == (413, 41301)
    conn.close()


def test_cli_accepts_only_loopback_hosts() -> None:
    assert loopback("127.0.0.1") == "127.0.0.1"
    assert loopback("::1") == "::1"
    assert loopback("localhost") == "localhost"
    for host in ("0.0.0.0", "10.0.0.5", "example.com"):  # noqa: S104 - checking that it is refused
        with pytest.raises(argparse.ArgumentTypeError):
            loopback(host)
