"""Avro schema parsing, validation and normalized form.

Covers the Avro 1.11 specification's types: primitives, record, enum, array, map, fixed and unions,
with named-type references (including recursive records). Logical types are kept as attributes of
their underlying type and do not change compatibility in M1.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

PRIMITIVES = ("null", "boolean", "int", "long", "float", "double", "bytes", "string")
MAX_SCHEMA_BYTES = 256 * 1024
MAX_DEPTH = 64
NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class SchemaError(ValueError):
    """The text is not a valid Avro schema."""


@dataclass(eq=False)
class Schema:
    """Base of every schema node."""

    type: str


@dataclass(eq=False)
class Primitive(Schema):
    """null, boolean, int, long, float, double, bytes or string."""


@dataclass(eq=False)
class Field:
    """A record field; has_default distinguishes a missing default from a null default."""

    name: str
    schema: Schema
    has_default: bool
    default: Any = None


@dataclass(eq=False)
class Record(Schema):
    """A named record; fields are filled after creation so a record can refer to itself."""

    name: str
    fields: list[Field] = field(default_factory=list)


@dataclass(eq=False)
class Enum(Schema):
    """A named enumeration with an optional default symbol for unknown values."""

    name: str
    symbols: list[str]
    default: str | None = None


@dataclass(eq=False)
class Array(Schema):
    """An array of items."""

    items: Schema


@dataclass(eq=False)
class Map(Schema):
    """A map from string to values."""

    values: Schema


@dataclass(eq=False)
class Fixed(Schema):
    """A named fixed-size byte sequence."""

    name: str
    size: int


@dataclass(eq=False)
class Union(Schema):
    """One of several branches."""

    branches: list[Schema]


def parse(text: str) -> Schema:
    """Parse and validate schema text; raise SchemaError with the reason."""
    if len(text.encode("utf-8")) > MAX_SCHEMA_BYTES:
        raise SchemaError(f"schema larger than {MAX_SCHEMA_BYTES} bytes")
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SchemaError(f"not JSON: {exc.msg} at line {exc.lineno} column {exc.colno}") from None
    return _Parser().node(raw, "", 0)


class _Parser:
    """Turns decoded JSON into schema nodes, resolving named types."""

    def __init__(self) -> None:
        self.named: dict[str, Schema] = {}

    def fullname(self, name: Any, namespace: Any, enclosing: str) -> str:
        """Full name of a named type, following the Avro namespace rules."""
        if not isinstance(name, str) or not name:
            raise SchemaError(f"named type needs a non-empty name, got {name!r}")
        if "." in name:
            full = name
        else:
            ns = namespace if isinstance(namespace, str) else enclosing
            full = f"{ns}.{name}" if ns else name
        if not all(NAME.match(part) for part in full.split(".")):
            raise SchemaError(f"invalid name {full!r}")
        if full in self.named or full in PRIMITIVES:
            raise SchemaError(f"type {full!r} is defined twice")
        return full

    def reference(self, name: str, namespace: str) -> Schema:
        """Resolve a type name used as a reference."""
        for candidate in (f"{namespace}.{name}" if namespace and "." not in name else name, name):
            if candidate in self.named:
                return self.named[candidate]
        raise SchemaError(f"unknown type {name!r}")

    def node(self, raw: Any, namespace: str, depth: int) -> Schema:
        """Parse one schema value."""
        if depth > MAX_DEPTH:
            raise SchemaError(f"schema nested deeper than {MAX_DEPTH} levels")
        if isinstance(raw, str):
            return Primitive(raw) if raw in PRIMITIVES else self.reference(raw, namespace)
        if isinstance(raw, list):
            return self.union(raw, namespace, depth)
        if not isinstance(raw, dict) or "type" not in raw:
            raise SchemaError(f"expected a type name, a union or an object with 'type', got {str(raw)[:40]!r}")
        kind = raw["type"]
        if kind in PRIMITIVES:
            return Primitive(kind)  # e.g. {"type": "long", "logicalType": "timestamp-millis"}
        if kind == "record" or kind == "error":
            return self.record(raw, namespace, depth)
        if kind == "enum":
            return self.enum(raw, namespace)
        if kind == "array":
            return Array("array", self.node(raw.get("items"), namespace, depth + 1))
        if kind == "map":
            return Map("map", self.node(raw.get("values"), namespace, depth + 1))
        if kind == "fixed":
            size = raw.get("size")
            if not isinstance(size, int) or isinstance(size, bool) or size < 0:
                raise SchemaError(f"fixed size must be a non-negative integer, got {size!r}")
            full = self.fullname(raw.get("name"), raw.get("namespace"), namespace)
            node = Fixed("fixed", full, size)
            self.named[full] = node
            return node
        if isinstance(kind, (dict, list)) or (isinstance(kind, str) and kind not in PRIMITIVES):
            return self.node(kind, namespace, depth + 1)  # {"type": {...}} or {"type": "SomeRecord"}
        raise SchemaError(f"unknown type {kind!r}")

    def union(self, raw: list[Any], namespace: str, depth: int) -> Union:
        """Parse a union: no nested unions, no two branches of the same type or name."""
        if not raw:
            raise SchemaError("a union needs at least one branch")
        branches = [self.node(b, namespace, depth + 1) for b in raw]
        seen: set[str] = set()
        for b in branches:
            if isinstance(b, Union):
                raise SchemaError("unions may not contain unions")
            key = type_key(b)
            if key in seen:
                raise SchemaError(f"union has two branches of type {key!r}")
            seen.add(key)
        return Union("union", branches)

    def record(self, raw: dict[str, Any], namespace: str, depth: int) -> Record:
        """Parse a record and its fields."""
        full = self.fullname(raw.get("name"), raw.get("namespace"), namespace)
        node = Record("record", full)
        self.named[full] = node  # registered before the fields, so fields may refer to it
        fields = raw.get("fields")
        if not isinstance(fields, list):
            raise SchemaError(f"record {full!r} needs a 'fields' list")
        inner_ns = full.rsplit(".", 1)[0] if "." in full else ""
        names: set[str] = set()
        for f in fields:
            if not isinstance(f, dict) or not isinstance(f.get("name"), str) or not NAME.match(f["name"]):
                raise SchemaError(f"record {full!r} has a field without a valid name")
            if f["name"] in names:
                raise SchemaError(f"record {full!r} has field {f['name']!r} twice")
            names.add(f["name"])
            schema = self.node(f.get("type"), inner_ns, depth + 1)
            has_default = "default" in f
            if has_default and not valid_default(schema, f["default"]):
                raise SchemaError(f"field {full}.{f['name']}: default {f['default']!r} does not match its type")
            node.fields.append(Field(f["name"], schema, has_default, f.get("default")))
        return node

    def enum(self, raw: dict[str, Any], namespace: str) -> Enum:
        """Parse an enum: unique, valid symbols; a default must be one of them."""
        full = self.fullname(raw.get("name"), raw.get("namespace"), namespace)
        symbols = raw.get("symbols")
        if not isinstance(symbols, list) or not symbols:
            raise SchemaError(f"enum {full!r} needs a non-empty 'symbols' list")
        if any(not isinstance(s, str) or not NAME.match(s) for s in symbols) or len(set(symbols)) != len(symbols):
            raise SchemaError(f"enum {full!r} has an invalid or repeated symbol")
        default = raw.get("default")
        if default is not None and default not in symbols:
            raise SchemaError(f"enum {full!r} default {default!r} is not a symbol")
        node = Enum("enum", full, list(symbols), default)
        self.named[full] = node
        return node


def type_key(s: Schema) -> str:
    """The name a union uses to tell branches apart."""
    if isinstance(s, (Record, Enum, Fixed)):
        return s.name
    return s.type


def valid_default(s: Schema, value: Any) -> bool:
    """Whether a JSON default value fits a schema (a union default must fit its first branch)."""
    if isinstance(s, Union):
        return valid_default(s.branches[0], value)
    if isinstance(s, Primitive):
        checks = {
            "null": value is None,
            "boolean": isinstance(value, bool),
            "int": isinstance(value, int) and not isinstance(value, bool) and -(2**31) <= value < 2**31,
            "long": isinstance(value, int) and not isinstance(value, bool) and -(2**63) <= value < 2**63,
            "float": isinstance(value, (int, float)) and not isinstance(value, bool),
            "double": isinstance(value, (int, float)) and not isinstance(value, bool),
            "bytes": isinstance(value, str),
            "string": isinstance(value, str),
        }
        return checks[s.type]
    if isinstance(s, Record):
        return isinstance(value, dict) and all(
            valid_default(f.schema, value[f.name]) if f.name in value else f.has_default for f in s.fields
        )
    if isinstance(s, Enum):
        return value in s.symbols
    if isinstance(s, Array):
        return isinstance(value, list) and all(valid_default(s.items, v) for v in value)
    if isinstance(s, Map):
        return isinstance(value, dict) and all(valid_default(s.values, v) for v in value.values())
    if isinstance(s, Fixed):
        return isinstance(value, str) and len(value) == s.size
    return False


def normalized(s: Schema) -> str:
    """Normalized JSON: Avro's Parsing Canonical Form plus field defaults and the enum default.

    Defaults are kept because they decide compatibility; docs, aliases and other attributes are
    dropped, names are full names, and a named type is written in full only the first time.
    """
    seen: set[str] = set()

    def walk(n: Schema) -> Any:
        if isinstance(n, Primitive):
            return n.type
        if isinstance(n, Union):
            return [walk(b) for b in n.branches]
        if isinstance(n, Array):
            return {"type": "array", "items": walk(n.items)}
        if isinstance(n, Map):
            return {"type": "map", "values": walk(n.values)}
        name = n.name  # type: ignore[attr-defined]
        if name in seen:
            return name
        seen.add(name)
        if isinstance(n, Record):
            fields = []
            for f in n.fields:
                item: dict[str, Any] = {"name": f.name, "type": walk(f.schema)}
                if f.has_default:
                    item["default"] = f.default
                fields.append(item)
            return {"name": name, "type": "record", "fields": fields}
        if isinstance(n, Enum):
            out: dict[str, Any] = {"name": name, "type": "enum", "symbols": n.symbols}
            if n.default is not None:
                out["default"] = n.default
            return out
        if isinstance(n, Fixed):
            return {"name": name, "type": "fixed", "size": n.size}
        raise TypeError(f"unexpected schema node {n!r}")

    return json.dumps(walk(s), separators=(",", ":"), ensure_ascii=False)
