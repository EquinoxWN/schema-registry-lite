"""Command line: `srl serve` runs the registry on a loopback address."""

from __future__ import annotations

import argparse
import ipaddress
import signal
import sys
import threading
from collections.abc import Sequence
from pathlib import Path

from .server import serve
from .store import Store


def loopback(host: str) -> str:
    """Refuse non-loopback hosts: M1 has no authentication or TLS."""
    if host == "localhost":
        return host
    try:
        if ipaddress.ip_address(host).is_loopback:
            return host
    except ValueError:
        pass
    raise argparse.ArgumentTypeError(f"{host!r} is not a loopback address; M1 serves on loopback only")


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments and serve until interrupted."""
    parser = argparse.ArgumentParser(prog="srl", description="Schema registry (Avro) with compatibility checks.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="run the HTTP API")
    s.add_argument("--db", type=Path, default=Path("data/registry.db"))
    s.add_argument("--host", type=loopback, default="127.0.0.1")
    s.add_argument("--port", type=int, default=8081)
    args = parser.parse_args(argv)
    server = serve(Store(args.db), args.host, args.port)
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"schema-registry-lite on http://{args.host}:{server.server_address[1]} (db {args.db})", file=sys.stderr)
    stop.wait()
    server.shutdown()
    server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
