"""The compatibility matrix: real-world schema changes and their verdict under each mode."""

from __future__ import annotations

import json
from typing import Any

import pytest
from conftest import fld, record
from hypothesis import given, settings
from hypothesis import strategies as st

from schema_registry_lite import avro
from schema_registry_lite.compat import Mode, can_read, check

BASE = [fld("id", "long"), fld("email", "string")]
ENUM = {"type": "enum", "name": "Tier", "symbols": ["FREE", "PRO"]}

# (change, old schema, new schema, backward ok, forward ok)
MATRIX: list[tuple[str, str, str, bool, bool]] = [
    ("no change", record(*BASE), record(*BASE), True, True),
    ("add field with default", record(*BASE), record(*BASE, fld("tier", "string", default="free")), True, True),
    ("add field without default", record(*BASE), record(*BASE, fld("age", "int")), False, True),
    ("remove field without default", record(*BASE, fld("age", "int")), record(*BASE), True, False),
    ("remove field with default", record(*BASE, fld("age", "int", default=0)), record(*BASE), True, True),
    ("rename field", record(*BASE), record(fld("id", "long"), fld("mail", "string")), False, False),
    ("int to long", record(fld("n", "int")), record(fld("n", "long")), True, False),
    ("long to int", record(fld("n", "long")), record(fld("n", "int")), False, True),
    ("float to double", record(fld("x", "float")), record(fld("x", "double")), True, False),
    ("int to double", record(fld("x", "int")), record(fld("x", "double")), True, False),
    ("string to bytes", record(fld("s", "string")), record(fld("s", "bytes")), True, True),
    ("string to int", record(fld("s", "string")), record(fld("s", "int")), False, False),
    (
        "make field nullable",
        record(fld("s", "string")),
        record(fld("s", ["null", "string"], default=None)),
        True,
        False,
    ),
    (
        "make nullable field required",
        record(fld("s", ["null", "string"], default=None)),
        record(fld("s", "string")),
        False,
        True,
    ),
    (
        "add union branch",
        record(fld("v", ["int", "string"])),
        record(fld("v", ["int", "string", "boolean"])),
        True,
        False,
    ),
    (
        "add enum symbol",
        record(fld("t", ENUM)),
        record(fld("t", {**ENUM, "symbols": ["FREE", "PRO", "TEAM"]})),
        True,
        False,
    ),
    (
        "add enum symbol, old reader has default",
        record(fld("t", {**ENUM, "default": "FREE"})),
        record(fld("t", {**ENUM, "symbols": ["FREE", "PRO", "TEAM"], "default": "FREE"})),
        True,
        True,
    ),
    ("remove enum symbol", record(fld("t", ENUM)), record(fld("t", {**ENUM, "symbols": ["FREE"]})), False, True),
    ("rename record", record(*BASE), record(*BASE, name="Customer"), False, False),
    ("change namespace", record(*BASE), record(*BASE, namespace="crm"), False, False),
    (
        "array items int to long",
        record(fld("a", {"type": "array", "items": "int"})),
        record(fld("a", {"type": "array", "items": "long"})),
        True,
        False,
    ),
    (
        "map values string to int",
        record(fld("m", {"type": "map", "values": "string"})),
        record(fld("m", {"type": "map", "values": "int"})),
        False,
        False,
    ),
    (
        "fixed size change",
        record(fld("h", {"type": "fixed", "name": "H", "size": 16})),
        record(fld("h", {"type": "fixed", "name": "H", "size": 32})),
        False,
        False,
    ),
    (
        "nested record adds field with default",
        record(fld("addr", {"type": "record", "name": "Addr", "fields": [fld("city", "string")]})),
        record(
            fld(
                "addr",
                {"type": "record", "name": "Addr", "fields": [fld("city", "string"), fld("zip", "string", default="")]},
            )
        ),
        True,
        True,
    ),
    (
        "nested record adds field without default",
        record(fld("addr", {"type": "record", "name": "Addr", "fields": [fld("city", "string")]})),
        record(
            fld("addr", {"type": "record", "name": "Addr", "fields": [fld("city", "string"), fld("zip", "string")]})
        ),
        False,
        True,
    ),
    ("change field default", record(fld("n", "int", default=1)), record(fld("n", "int", default=2)), True, True),
    (
        "type to array of type",
        record(fld("t", "string")),
        record(fld("t", {"type": "array", "items": "string"})),
        False,
        False,
    ),
    (
        "recursive list adds field with default",
        record(fld("v", "int"), fld("next", ["null", "Node"], default=None), name="Node"),
        record(fld("v", "int"), fld("next", ["null", "Node"], default=None), fld("w", "int", default=0), name="Node"),
        True,
        True,
    ),
]


@pytest.mark.parametrize(("change", "old", "new", "backward", "forward"), MATRIX, ids=[m[0] for m in MATRIX])
def test_matrix(change: str, old: str, new: str, backward: bool, forward: bool) -> None:
    o, n = avro.parse(old), avro.parse(new)
    assert (check(n, [o], Mode.BACKWARD) == []) is backward, check(n, [o], Mode.BACKWARD)
    assert (check(n, [o], Mode.FORWARD) == []) is forward, check(n, [o], Mode.FORWARD)
    assert (check(n, [o], Mode.FULL) == []) is (backward and forward)
    assert check(n, [o], Mode.NONE) == []


def test_reasons_name_the_field_and_the_rule() -> None:
    old = avro.parse(record(*BASE))
    problems = check(avro.parse(record(*BASE, fld("age", "int"))), [old], Mode.BACKWARD)
    assert problems == ["backward vs latest: $.age: reader field has no default and the writer does not have it"]
    nested = check(
        avro.parse(record(fld("a", {"type": "array", "items": "int"}))),
        [avro.parse(record(fld("a", {"type": "array", "items": "long"})))],
        Mode.BACKWARD,
    )
    assert nested == ["backward vs latest: $.a[]: writer type 'long' cannot be read as 'int'"]


def test_transitive_modes_check_every_earlier_version() -> None:
    v1 = avro.parse(record(*BASE))
    v2 = avro.parse(record(*BASE, fld("age", "int", default=0)))
    v3 = avro.parse(record(*BASE, fld("age", "int")))  # drops the default: fine against v2, not v1
    assert check(v3, [v1, v2], Mode.BACKWARD) == []
    problems = check(v3, [v1, v2], Mode.BACKWARD_TRANSITIVE)
    assert problems == ["backward vs latest-1: $.age: reader field has no default and the writer does not have it"]
    assert check(v3, [v1, v2], Mode.FULL_TRANSITIVE) != []
    assert check(v3, [], Mode.FULL_TRANSITIVE) == []


# Random schemas from a small grammar, to check properties that hold for every schema.
PRIMS = st.sampled_from(["null", "boolean", "int", "long", "float", "double", "bytes", "string"])


def _types(depth: int) -> st.SearchStrategy[Any]:
    """Field types: primitives, arrays and maps nested up to depth."""
    if depth == 0:
        return PRIMS
    inner = _types(depth - 1)
    return st.one_of(
        PRIMS, inner.map(lambda t: {"type": "array", "items": t}), inner.map(lambda t: {"type": "map", "values": t})
    )


SCHEMAS = st.lists(_types(2), min_size=1, max_size=6).map(
    lambda ts: json.dumps(
        {"type": "record", "name": "R", "fields": [{"name": f"f{i}", "type": t} for i, t in enumerate(ts)]}
    )
)


@given(SCHEMAS)
@settings(max_examples=200)
def test_every_schema_can_read_itself(text: str) -> None:
    s = avro.parse(text)
    assert can_read(s, s) == []
    assert check(s, [s], Mode.FULL_TRANSITIVE) == []


@given(SCHEMAS)
@settings(max_examples=200)
def test_adding_a_defaulted_field_is_always_fully_compatible(text: str) -> None:
    raw = json.loads(text)
    raw["fields"].append({"name": "extra", "type": "string", "default": ""})
    old, new = avro.parse(text), avro.parse(json.dumps(raw))
    assert check(new, [old], Mode.FULL) == []


@given(SCHEMAS)
@settings(max_examples=200)
def test_adding_a_required_field_is_never_backward_compatible(text: str) -> None:
    raw = json.loads(text)
    raw["fields"].append({"name": "extra", "type": "string"})
    assert check(avro.parse(json.dumps(raw)), [avro.parse(text)], Mode.BACKWARD) != []
