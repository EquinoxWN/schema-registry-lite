# RFC 0001: schema-registry-lite design

- **Status:** Accepted (M1 built)
- **Author:** EquinoxWN
- **Created:** 2026-10-18

## Problem (Define)

Events outlive the code that wrote them. When a producer changes an event schema (renames a field,
adds a required field, narrows a type), every consumer still reading old events, and every consumer
not yet upgraded, can break, often silently. A schema registry stops this at the source: producers
register each new schema, and the registry refuses versions that break the agreed compatibility
mode, before any event is written with them.

Users: teams producing and consuming Avro events (Kafka or other logs); platform engineers who
own the event contracts.

### Critical-to-quality measures

| CTQ | Target (M1) | How it is measured |
|---|---|---|
| Correct verdicts | the expected backward and forward verdict for every case of a 28-case matrix of real-world changes | `tests/test_compat.py` |
| Properties hold for any schema | every schema reads itself; adding a defaulted field is fully compatible; adding a required field is never backward-compatible | Hypothesis, 200 random schemas each |
| Stable identity | the same schema (any formatting) gets the same global ID under every subject; re-registering returns the existing version | `tests/test_store.py`, `tests/test_server.py` |
| No lost or duplicate versions | concurrent registrations get distinct, consecutive versions | 12 threads in `tests/test_store.py` |
| Explainable rejections | each rejection lists the path and the rule (`$.age: reader field has no default ...`) | tests and the demo |
| Compatible with client expectations | Confluent REST paths, response shapes and error codes for the endpoints producers use | `tests/test_server.py` |

## Goals

- M1: producers register an Avro schema under a subject and get a global ID and a version; each new
  version is checked against earlier ones under NONE, BACKWARD, FORWARD, FULL or their TRANSITIVE
  variants; modes are set globally or per subject.
- The Confluent Schema Registry REST paths for registering, reading, looking up, testing
  compatibility and configuring modes, so standard clients can talk to it (verified with a real
  client in M3).

## Non-goals

- Protobuf and JSON Schema (M2), schema references, deleting subjects or versions.
- Authentication, TLS and multi-node deployment: M1 serves on loopback only.
- Postgres: SQLite in M1 ([ADR 0002](../adr/0002-stdlib-http-and-sqlite.md)).

## Design

```
POST /subjects/{s}/versions {"schema": "..."}
  -> avro.parse (validate, limits) -> avro.normalized (identity)
  -> store.register: BEGIN IMMEDIATE
       same normalized schema already under {s}? -> return its id and version
       compat.check(new, versions the mode compares against) -> problems? -> 409 with reasons
       insert schema (or reuse global id) + version = last + 1 -> COMMIT
  -> {"id": n}
```

- **avro.py:** parses the Avro 1.11 types, resolves named types and namespaces (recursive records
  included), validates names, unions, enum symbols, fixed sizes and default values, limits size
  (256 KiB) and depth (64), and produces a normalized form (Parsing Canonical Form plus defaults).
- **compat.py:** Avro schema resolution: same type or a legal promotion (int to long/float/double,
  long to float/double, float to double, string and bytes both ways); records matched by full name,
  each reader field found in the writer or defaulted; enums need the writer's symbols or a reader
  default; arrays, maps, fixed and unions on either side. BACKWARD = new reads old, FORWARD = old
  reads new, FULL = both; TRANSITIVE compares with every earlier version, otherwise only the latest.
- **store.py:** SQLite with three tables (schemas, versions, config); the check and the insert run
  in one `BEGIN IMMEDIATE` transaction so concurrent registrations serialize; only the versions the
  mode compares against are parsed.
- **server.py:** stdlib `ThreadingHTTPServer`; 1 MiB body limit (refused before reading); Confluent
  error codes (409 incompatible, 42201 invalid schema, 40401/40402/40403 not found); no tracebacks
  in responses.

## Alternatives considered

| Option | Why not (yet) |
|---|---|
| Run Confluent Schema Registry or Apicurio | The right production choice, but it needs Kafka or a database plus a JVM, and the goal here is to build and test the compatibility rules. |
| FastAPI + Postgres (the original plan) | Better for multi-node and validation, but adds a web framework, a driver and a database server for an API of 11 routes; see ADR 0002. |
| Identity by raw text or by Parsing Canonical Form only | Raw text gives two IDs for the same schema with different whitespace; PCF drops defaults, which change compatibility. See [ADR 0003](../adr/0003-identity-by-normalized-form.md). |
| Check compatibility by serializing sample data with both schemas | Catches what the samples cover only; the resolution rules decide for all data. |

## Measurement plan (Measure)

- Tests: the matrix, properties, storage, concurrency and the HTTP API.
- `make bench`: registration latency over 200 versions of one subject, BACKWARD and
  FULL_TRANSITIVE.
- M3: the matrix grows to dozens of cases per format and is published; a standard client is run
  against the endpoints.

## Milestones

- **M1 (done):** RFC; registration with global IDs and versions; compatibility modes including
  transitive.
- **M2:** per-format rules (Protobuf field numbers, Avro defaults); rejections naming the exact
  field and rule.
- **M3:** CLI and GitHub Action; compatibility matrix with dozens of cases per format; proof with a
  standard registry client.

## Risks and open questions

- Aliases are ignored: a field renamed with an alias is reported as incompatible (stricter than the
  Avro specification allows).
- Logical types do not affect compatibility in M1 (a decimal with another scale is accepted).
- SQLite serializes writers; registrations are rare, but the store would need Postgres for several
  registry processes.
- Error messages already name the path and rule; stable rule IDs and Protobuf rules are M2.
