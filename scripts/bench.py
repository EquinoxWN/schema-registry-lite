"""Registration latency as a subject's history grows, under BACKWARD and FULL_TRANSITIVE.

usage: python scripts/bench.py [--versions 200]
Each version adds one field with a default, so every registration is accepted. Uses a temporary
SQLite file under .tmp/.
"""

from __future__ import annotations

import argparse
import json
import statistics
import tempfile
import time
from pathlib import Path

from schema_registry_lite.compat import Mode
from schema_registry_lite.store import Store


def schema(i: int) -> str:
    """Version i: an id field plus i defaulted fields."""
    fields = [{"name": "id", "type": "long"}] + [{"name": f"f{j}", "type": "int", "default": 0} for j in range(i)]
    return json.dumps({"type": "record", "name": "Event", "namespace": "bench", "fields": fields})


def run(mode: Mode, versions: int, folder: Path) -> list[float]:
    """Register the versions one by one and return each call's time in ms."""
    store = Store(folder / f"{mode.value}.db")
    store.set_mode("events", mode)
    times = []
    for i in range(versions):
        t0 = time.perf_counter()
        store.register("events", schema(i))
        times.append((time.perf_counter() - t0) * 1000)
    return times


def main() -> None:
    """Print one row per mode and quarter of the history."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--versions", type=int, default=200)
    n = parser.parse_args().versions
    print(f"{n} versions per subject; version i has i + 1 fields; SQLite file on local disk")
    print("| Mode | Versions 1-50 p50 ms | Last 50 p50 ms | Last 50 p95 ms | Total s |")
    print("|---|---|---|---|---|")
    Path(".tmp").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=".tmp") as tmp:
        for mode in (Mode.BACKWARD, Mode.FULL_TRANSITIVE):
            t = run(mode, n, Path(tmp))
            last = t[-50:]
            p95 = statistics.quantiles(last, n=20)[-1]
            print(
                f"| {mode.value} | {statistics.median(t[:50]):.2f} | {statistics.median(last):.2f} | "
                f"{p95:.2f} | {sum(t) / 1000:.2f} |"
            )


if __name__ == "__main__":
    main()
