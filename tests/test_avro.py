"""Avro parsing, validation and the normalized form."""

from __future__ import annotations

import json

import pytest
from conftest import fld, record

from schema_registry_lite import avro


@pytest.mark.parametrize(
    "text",
    [
        '"string"',
        '{"type": "long", "logicalType": "timestamp-millis"}',
        '["null", "string"]',
        record(fld("id", "long"), fld("tags", {"type": "array", "items": "string"}, default=[])),
        record(fld("attrs", {"type": "map", "values": "int"}, default={})),
        record(fld("tier", {"type": "enum", "name": "Tier", "symbols": ["FREE", "PRO"], "default": "FREE"})),
        record(fld("hash", {"type": "fixed", "name": "Md5", "size": 16})),
        record(fld("value", "int"), fld("next", ["null", "Node"], default=None), name="Node"),  # recursive
        record(fld("a", {"type": "record", "name": "Inner", "fields": []}), fld("b", "Inner")),  # reference
        record(fld("a", {"type": "record", "name": "x.Inner", "fields": []}), fld("b", "x.Inner")),
        record(fld("n", ["int", "null"], default=3)),
        record(fld("d", "double", default=1)),
    ],
)
def test_valid_schemas_parse(text: str) -> None:
    avro.parse(text)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("{not json", "not JSON"),
        ('"varchar"', "unknown type"),
        ("[]", "at least one branch"),
        ('["string", "string"]', "two branches"),
        ('["null", ["int"]]', "may not contain unions"),
        ('{"type": "record", "name": "X"}', "'fields' list"),
        ('{"type": "record", "name": "1X", "fields": []}', "invalid name"),
        (record(fld("a", "int"), fld("a", "int")), "twice"),
        (record(fld("a", "int", default="x")), "does not match"),
        (record(fld("a", ["null", "int"], default=1)), "does not match"),  # union default fits the first branch
        (record(fld("a", "int", default=2**31)), "does not match"),
        ('{"type": "enum", "name": "E", "symbols": ["A", "A"]}', "repeated"),
        ('{"type": "enum", "name": "E", "symbols": ["A"], "default": "B"}', "not a symbol"),
        ('{"type": "fixed", "name": "F", "size": -1}', "non-negative"),
        (record(fld("a", {"type": "record", "name": "User", "fields": []})), "defined twice"),
        ('{"type": "array"}', "expected a type"),
    ],
)
def test_invalid_schemas_are_refused_with_a_reason(text: str, message: str) -> None:
    with pytest.raises(avro.SchemaError, match=message):
        avro.parse(text)


def test_size_and_depth_limits() -> None:
    with pytest.raises(avro.SchemaError, match="larger than"):
        avro.parse(json.dumps("x" * avro.MAX_SCHEMA_BYTES))
    deep: object = "int"
    for _ in range(avro.MAX_DEPTH + 2):
        deep = {"type": "array", "items": deep}
    with pytest.raises(avro.SchemaError, match="deeper than"):
        avro.parse(json.dumps(deep))


def test_normalized_form_ignores_docs_whitespace_and_order_of_attributes() -> None:
    a = (
        '{"type":"record","name":"User","namespace":"shop","doc":"a user",'
        '"fields":[{"name":"id","type":"long","doc":"x"}]}'
    )
    b = '{ "fields": [ {"type": "long", "name": "id"} ], "name": "shop.User", "type": "record" }'
    assert avro.normalized(avro.parse(a)) == avro.normalized(avro.parse(b))
    assert (
        avro.normalized(avro.parse(a)) == '{"name":"shop.User","type":"record","fields":[{"name":"id","type":"long"}]}'
    )


def test_normalized_form_keeps_defaults_and_writes_named_types_once() -> None:
    text = record(fld("a", {"type": "enum", "name": "E", "symbols": ["X"]}), fld("b", "E", default="X"))
    norm = avro.normalized(avro.parse(text))
    assert norm.count('"symbols"') == 1
    assert '"default":"X"' in norm
    with_default = avro.normalized(avro.parse(record(fld("a", "int", default=1))))
    assert with_default != avro.normalized(avro.parse(record(fld("a", "int"))))
