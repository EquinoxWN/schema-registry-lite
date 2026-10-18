"""Avro schema resolution: can data written with one schema be read with another?

Implements the resolution rules of the Avro specification: matching types, numeric and
string/bytes promotions, records matched by field name with defaults for missing fields, enum
symbols, array items, map values, fixed size, and unions on either side.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum

from .avro import Array, Enum, Fixed, Map, Primitive, Record, Schema, Union, type_key

PROMOTIONS = {
    "int": {"long", "float", "double"},
    "long": {"float", "double"},
    "float": {"double"},
    "string": {"bytes"},
    "bytes": {"string"},
}


class Mode(StrEnum):
    """Compatibility modes, with the Confluent Schema Registry names."""

    NONE = "NONE"
    BACKWARD = "BACKWARD"
    FORWARD = "FORWARD"
    FULL = "FULL"
    BACKWARD_TRANSITIVE = "BACKWARD_TRANSITIVE"
    FORWARD_TRANSITIVE = "FORWARD_TRANSITIVE"
    FULL_TRANSITIVE = "FULL_TRANSITIVE"


def can_read(reader: Schema, writer: Schema) -> list[str]:
    """Problems that stop reader from reading data written with writer; empty means compatible."""
    problems: list[str] = []
    _check(reader, writer, "$", problems, set())
    return problems


def _check(reader: Schema, writer: Schema, path: str, out: list[str], active: set[tuple[int, int]]) -> None:
    """Compare one pair of nodes, appending problems with their path."""
    if isinstance(writer, Union):
        for branch in writer.branches:  # every value the writer may produce must be readable
            _check(reader, branch, f"{path}<{type_key(branch)}>", out, active)
        return
    if isinstance(reader, Union):
        for branch in reader.branches:
            trial: list[str] = []
            _check(branch, writer, path, trial, active)
            if not trial:
                return
        options = [type_key(b) for b in reader.branches]
        out.append(f"{path}: reader union {options} has no branch for writer type {type_key(writer)!r}")
        return
    if isinstance(reader, Primitive) and isinstance(writer, Primitive):
        if reader.type != writer.type and reader.type not in PROMOTIONS.get(writer.type, set()):
            out.append(f"{path}: writer type {writer.type!r} cannot be read as {reader.type!r}")
        return
    if type(reader) is not type(writer):
        out.append(f"{path}: writer type {type_key(writer)!r} cannot be read as {type_key(reader)!r}")
        return
    if isinstance(reader, Record) and isinstance(writer, Record):
        _records(reader, writer, path, out, active)
    elif isinstance(reader, Enum) and isinstance(writer, Enum):
        if reader.name != writer.name:
            out.append(f"{path}: enum name {writer.name!r} differs from {reader.name!r}")
        missing = [s for s in writer.symbols if s not in reader.symbols]
        if missing and reader.default is None:
            out.append(f"{path}: reader enum {reader.name!r} lacks symbols {missing} and has no default")
    elif isinstance(reader, Array) and isinstance(writer, Array):
        _check(reader.items, writer.items, f"{path}[]", out, active)
    elif isinstance(reader, Map) and isinstance(writer, Map):
        _check(reader.values, writer.values, f"{path}{{}}", out, active)
    elif (
        isinstance(reader, Fixed)
        and isinstance(writer, Fixed)
        and (reader.name != writer.name or reader.size != writer.size)
    ):
        out.append(f"{path}: fixed {writer.name!r}({writer.size}) cannot be read as {reader.name!r}({reader.size})")


def _records(reader: Record, writer: Record, path: str, out: list[str], active: set[tuple[int, int]]) -> None:
    """Records: names must match; each reader field needs a writer field or a default."""
    if reader.name != writer.name:
        out.append(f"{path}: record name {writer.name!r} differs from {reader.name!r}")
        return
    key = (id(reader), id(writer))
    if key in active:
        return  # recursive type already being compared on this path
    active.add(key)
    written = {f.name: f for f in writer.fields}
    for f in reader.fields:
        if f.name in written:
            _check(f.schema, written[f.name].schema, f"{path}.{f.name}", out, active)
        elif not f.has_default:
            out.append(f"{path}.{f.name}: reader field has no default and the writer does not have it")
    active.discard(key)


def check(new: Schema, previous: Sequence[Schema], mode: Mode) -> list[str]:
    """Problems with registering new after the previous versions (oldest first) under a mode."""
    if mode == Mode.NONE or not previous:
        return []
    against = previous if mode.endswith("_TRANSITIVE") else previous[-1:]
    problems: list[str] = []
    for i, old in enumerate(reversed(against)):
        label = "latest" if i == 0 else f"latest-{i}"
        if mode in (Mode.BACKWARD, Mode.BACKWARD_TRANSITIVE, Mode.FULL, Mode.FULL_TRANSITIVE):
            problems += [f"backward vs {label}: {p}" for p in can_read(new, old)]
        if mode in (Mode.FORWARD, Mode.FORWARD_TRANSITIVE, Mode.FULL, Mode.FULL_TRANSITIVE):
            problems += [f"forward vs {label}: {p}" for p in can_read(old, new)]
    return problems
