# ADR 0002: Standard-library HTTP server and SQLite instead of FastAPI and Postgres

- **Status:** Accepted

## Context

The scaffold planned FastAPI and Postgres. M1 needs 11 routes, a few hundred schemas at most, one
process on loopback, and transactions that make "check, then add a version" atomic.

## Decision

The registry uses only the Python standard library: `http.server.ThreadingHTTPServer` for the API
and `sqlite3` (WAL mode) for storage. Registration runs in one `BEGIN IMMEDIATE` transaction that
reads the history, checks compatibility and inserts the version, so concurrent writers serialize.

## Consequences

- No runtime dependencies: `pip install` pulls nothing, and the dependency audit covers only the
  dev tools. Setup needs no database server, which also keeps Docker out of M1.
- Writes are serialized per database file. Measured: a registration takes about 16 to 33 ms under
  BACKWARD (200 versions, schemas growing to 201 fields), mostly the SQLite commit; enough for a
  registry, where writes are rare and reads can be cached by clients.
- One registry process per database file. Running several replicas (or a managed database) means
  replacing `Store` with a Postgres implementation behind the same methods.
- `http.server` has no request validation layer, so the handler validates bodies itself (size limit
  before reading, JSON object check, schema string check) and never returns tracebacks.
- The stdlib server is not hardened for direct internet exposure; M1 refuses non-loopback addresses.
