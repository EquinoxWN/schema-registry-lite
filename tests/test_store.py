"""Registry storage: IDs, versions, idempotency, modes and concurrent registration."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from conftest import fld, record

from schema_registry_lite.avro import SchemaError
from schema_registry_lite.compat import Mode
from schema_registry_lite.store import IncompatibleError, NotFoundError, Store

V1 = record(fld("id", "long"))
V2 = record(fld("id", "long"), fld("tier", "string", default="free"))


def test_versions_count_per_subject_and_ids_are_global(store: Store) -> None:
    a1 = store.register("users-value", V1)
    a2 = store.register("users-value", V2)
    assert (a1.version, a2.version) == (1, 2)
    assert a1.id != a2.id
    b1 = store.register("audit-value", V2)  # same schema, another subject: same global ID, version 1
    assert (b1.id, b1.version) == (a2.id, 1)
    assert store.subjects() == ["audit-value", "users-value"]
    assert store.versions("users-value") == [1, 2]
    assert store.version("users-value", "latest").id == a2.id
    assert json.loads(store.schema(a1.id)) == json.loads(V1)


def test_registering_the_same_schema_again_is_idempotent(store: Store) -> None:
    first = store.register("s", V1)
    pretty = json.dumps(json.loads(V1), indent=2)  # same schema, other formatting
    again = store.register("s", pretty)
    assert (again.id, again.version, again.created) == (first.id, 1, False)
    assert store.versions("s") == [1]


def test_incompatible_schema_is_rejected_and_nothing_is_stored(store: Store) -> None:
    store.register("s", V1)
    with pytest.raises(IncompatibleError) as exc:
        store.register("s", record(fld("id", "long"), fld("age", "int")))
    assert exc.value.problems == [
        "backward vs latest: $.age: reader field has no default and the writer does not have it"
    ]
    assert store.versions("s") == [1]


def test_modes_per_subject_and_global(store: Store) -> None:
    assert store.mode("s") is Mode.BACKWARD
    store.set_mode(None, Mode.FULL)
    assert store.mode("s") is Mode.FULL
    store.set_mode("s", Mode.NONE)
    assert store.mode("s") is Mode.NONE
    assert store.mode("other") is Mode.FULL
    store.register("s", V1)
    store.register("s", record(fld("x", "string")))  # anything goes under NONE
    assert store.versions("s") == [1, 2]


def test_compatibility_preview_does_not_register(store: Store) -> None:
    store.register("s", V1)
    assert store.compatibility("s", V2) == []
    assert store.compatibility("s", record(fld("id", "string"))) != []
    assert store.versions("s") == [1]


def test_lookup_and_not_found(store: Store) -> None:
    reg = store.register("s", V1)
    assert store.lookup("s", V1).version == reg.version
    with pytest.raises(NotFoundError):
        store.lookup("s", V2)
    with pytest.raises(NotFoundError):
        store.versions("missing")
    with pytest.raises(NotFoundError):
        store.version("s", "9")
    with pytest.raises(NotFoundError):
        store.schema(999)


@pytest.mark.parametrize("subject", ["", "a/b", "a b", "x" * 256, "../etc"])
def test_bad_subject_names_are_refused(store: Store, subject: str) -> None:
    with pytest.raises(ValueError, match="invalid subject"):
        store.register(subject, V1)


def test_invalid_schema_is_refused(store: Store) -> None:
    with pytest.raises(SchemaError):
        store.register("s", '{"type": "record"}')


def test_concurrent_registrations_get_distinct_consecutive_versions(store: Store) -> None:
    schemas = [record(fld("id", "long"), *[fld(f"f{j}", "int", default=0) for j in range(i)]) for i in range(12)]
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda s: store.register("s", s), schemas))
    assert sorted(r.version for r in results) == list(range(1, 13))
    assert len({r.id for r in results}) == 12
    assert store.versions("s") == list(range(1, 13))


def test_data_survives_a_restart(store: Store) -> None:
    reg = store.register("s", V1)
    reopened = Store(store.path)
    assert reopened.version("s", "1").id == reg.id


def test_re_registering_an_existing_version_skips_the_check_even_after_a_mode_change(store: Store) -> None:
    store.register("s", V1)
    store.register("s", V2)
    store.set_mode("s", Mode.FULL_TRANSITIVE)
    again = store.register("s", V1)  # already version 1: returned as is, as Confluent does
    assert (again.version, again.created) == (1, False)
