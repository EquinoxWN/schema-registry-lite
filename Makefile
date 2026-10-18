.PHONY: setup lint test audit demo bench serve ci

PY ?= python

setup:
	$(PY) -m pip install --upgrade pip && $(PY) -m pip install -e ".[dev]"

# ruff, ruff format and mypy --strict (src, tests and scripts).
lint:
	$(PY) -m ruff check . && $(PY) -m ruff format --check . && $(PY) -m mypy

# Avro parsing, the compatibility matrix and properties, storage, and the HTTP API over loopback.
test:
	$(PY) -m pytest -q

# Known vulnerabilities in the installed Python dependencies (runtime has none; dev tools only).
audit:
	$(PY) -m pip_audit --skip-editable --cache-dir .tmp/pip-audit

# Start the registry on a free loopback port, evolve a schema through the API, stop it.
demo:
	mkdir -p .tmp && $(PY) scripts/demo.py

# Registration latency as a subject grows to 200 versions, BACKWARD vs FULL_TRANSITIVE.
bench:
	mkdir -p .tmp && $(PY) scripts/bench.py

serve:
	srl serve --db data/registry.db --port 8081

ci: setup lint test
