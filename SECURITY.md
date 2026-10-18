# Security policy

## Reporting a vulnerability

Please report security problems privately through GitHub:
**Security > Report a vulnerability** on this repository
(`https://github.com/EquinoxWN/schema-registry-lite/security/advisories/new`). Do not open a public issue.

Include what you found, how to reproduce it, and the impact you expect. You will get an answer
within 7 days. Fixes are released as soon as they are ready, and you are credited unless you ask
not to be.

## Supported versions

This project is pre-1.0. Only the latest commit on `main` receives fixes.

## Scope

- **In scope:** a breaking schema accepted under a mode that should refuse it, crafted schemas or
  request bodies that crash the server or use unbounded memory or time, version numbers lost or
  duplicated under concurrency, SQL injection, and the server listening beyond loopback.
- **Out of scope:** schemas you register yourself under mode NONE.

## How this repository protects itself

- The server refuses non-loopback addresses; M1 has no authentication or TLS.
- Request bodies over 1 MiB are refused from the Content-Length header, before reading; schemas
  over 256 KiB or nested deeper than 64 levels are refused; subject names are limited to letters,
  digits, `.`, `_` and `-`.
- Every SQL statement uses parameters; registration runs in one serialized transaction.
- Errors return Confluent-style codes without tracebacks.
- No runtime dependencies (standard library only); dev tools are audited with pip-audit.
- Every GitHub Action is pinned to a full commit SHA, and workflows run with read-only
  permissions and without persisted credentials.
- Dependabot update pull requests are paused (`open-pull-requests-limit: 0`) so the repository keeps a single `main` branch; dependencies are updated by hand and checked by the CI audit.
- CI runs lint, strict type checks, tests and a known-vulnerability check (pip-audit) on every push and pull request.
