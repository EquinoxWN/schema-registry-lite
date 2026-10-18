# Contributing

Thanks for helping. This is a Python project with no runtime dependencies; issues and pull requests
are welcome.

## Set up and check your change

Needs Python 3.11+ (CI uses 3.12) and make. Use a virtualenv inside the repo (`.venv/`, git-ignored).

```bash
python -m venv .venv && . .venv/bin/activate   # Windows: .venv\Scripts\activate
make setup   # pip install -e ".[dev]"
make lint    # ruff, ruff format --check, mypy --strict
make test    # pytest (warnings are errors)
make audit   # pip-audit, as in CI
make demo    # the HTTP API on a temporary database
make bench   # registration latency over 200 versions
```

A pull request is ready when `make lint` and `make test` pass and CI is green.

## Guidelines

- Every compatibility rule change needs rows in the matrix in `tests/test_compat.py`, with the
  expected backward and forward verdicts and the reason they are right.
- Keep the REST paths, response shapes and error codes compatible with the Confluent Schema
  Registry API; add a test in `tests/test_server.py` for any new route.
- Keep the runtime on the standard library; a new dependency needs an ADR.
- Keep function comments to one short phrase; let names and tests explain the rest.
- Report security problems privately, as described in [SECURITY.md](SECURITY.md).

By contributing you agree that your work is released under the [MIT License](LICENSE).
