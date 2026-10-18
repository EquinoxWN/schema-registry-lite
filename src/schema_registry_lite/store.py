"""Registry storage in SQLite: global schema IDs, per-subject versions and compatibility modes."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from . import avro
from .compat import Mode, check

SUBJECT = re.compile(r"^[A-Za-z0-9._-]{1,255}$")
DEFAULT_MODE = Mode.BACKWARD

DDL = """
CREATE TABLE IF NOT EXISTS schemas (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  normalized TEXT NOT NULL UNIQUE,
  schema TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS versions (
  subject TEXT NOT NULL,
  version INTEGER NOT NULL,
  schema_id INTEGER NOT NULL REFERENCES schemas(id),
  PRIMARY KEY (subject, version)
);
CREATE TABLE IF NOT EXISTS config (
  subject TEXT PRIMARY KEY,
  mode TEXT NOT NULL
);
"""


class NotFoundError(LookupError):
    """A subject, version or schema ID does not exist."""


class IncompatibleError(ValueError):
    """The schema breaks the subject's compatibility mode; problems say where and why."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class Registered:
    """Result of a registration."""

    id: int
    version: int
    created: bool


@dataclass(frozen=True)
class Version:
    """One version of a subject."""

    subject: str
    version: int
    id: int
    schema: str


def check_subject(subject: str) -> str:
    """Refuse subject names outside letters, digits, dot, underscore and hyphen."""
    if not SUBJECT.match(subject):
        raise ValueError(f"invalid subject name {subject[:40]!r}")
    return subject


def _needed(history: list[tuple[int, int, str]], mode: Mode) -> list[avro.Schema]:
    """Parse only the versions the mode compares against: all of them, the latest, or none."""
    if mode == Mode.NONE:
        return []
    texts = [t for _, _, t in history]
    return [avro.parse(t) for t in (texts if mode.value.endswith("_TRANSITIVE") else texts[-1:])]


class Store:
    """SQLite-backed registry; safe for concurrent use (one connection per operation, WAL)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(DDL)

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        """An autocommit connection; register() opens its own explicit transaction."""
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        try:
            db.execute("PRAGMA foreign_keys=ON")
            yield db
        finally:
            db.close()

    def mode(self, subject: str | None) -> Mode:
        """The subject's mode, else the global mode, else BACKWARD."""
        with self._db() as db:
            for key in ([subject] if subject else []) + [""]:
                row = db.execute("SELECT mode FROM config WHERE subject = ?", (key,)).fetchone()
                if row:
                    return Mode(row[0])
        return DEFAULT_MODE

    def set_mode(self, subject: str | None, mode: Mode) -> None:
        """Set the mode for one subject, or the global mode when subject is None."""
        key = check_subject(subject) if subject else ""
        with self._db() as db:
            db.execute(
                "INSERT INTO config(subject, mode) VALUES (?, ?) "
                "ON CONFLICT(subject) DO UPDATE SET mode = excluded.mode",
                (key, mode.value),
            )

    def _history(self, db: sqlite3.Connection, subject: str) -> list[tuple[int, int, str]]:
        """(version, id, schema text) of a subject, oldest first."""
        rows = db.execute(
            "SELECT v.version, s.id, s.schema FROM versions v JOIN schemas s ON s.id = v.schema_id "
            "WHERE v.subject = ? ORDER BY v.version",
            (subject,),
        ).fetchall()
        return [(int(r[0]), int(r[1]), str(r[2])) for r in rows]

    def compatibility(self, subject: str, text: str) -> list[str]:
        """Problems registering text under subject would hit (empty when it would be accepted)."""
        check_subject(subject)
        schema = avro.parse(text)
        mode = self.mode(subject)
        with self._db() as db:
            previous = _needed(self._history(db, subject), mode)
        return check(schema, previous, mode)

    def register(self, subject: str, text: str) -> Registered:
        """Register a schema version; the same schema again returns its existing ID and version."""
        check_subject(subject)
        schema = avro.parse(text)
        norm = avro.normalized(schema)
        mode = self.mode(subject)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")  # one writer at a time: version numbers never collide
            try:
                history = self._history(db, subject)
                row = db.execute("SELECT id FROM schemas WHERE normalized = ?", (norm,)).fetchone()
                if row:
                    for version, sid, _ in history:
                        if sid == row[0]:
                            db.execute("ROLLBACK")
                            return Registered(id=sid, version=version, created=False)
                problems = check(schema, _needed(history, mode), mode)
                if problems:
                    raise IncompatibleError(problems)
                if row:
                    schema_id = int(row[0])
                else:
                    cur = db.execute("INSERT INTO schemas(normalized, schema) VALUES (?, ?)", (norm, text))
                    schema_id = int(cur.lastrowid or 0)
                version = (history[-1][0] if history else 0) + 1
                db.execute(
                    "INSERT INTO versions(subject, version, schema_id) VALUES (?, ?, ?)", (subject, version, schema_id)
                )
                db.execute("COMMIT")
                return Registered(id=schema_id, version=version, created=True)
            except BaseException:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise

    def lookup(self, subject: str, text: str) -> Version:
        """The version of subject holding this schema (compared in normalized form)."""
        norm = avro.normalized(avro.parse(text))
        with self._db() as db:
            row = db.execute(
                "SELECT v.version, s.id, s.schema FROM versions v JOIN schemas s ON s.id = v.schema_id "
                "WHERE v.subject = ? AND s.normalized = ?",
                (check_subject(subject), norm),
            ).fetchone()
        if not row:
            raise NotFoundError(f"schema not found under subject {subject!r}")
        return Version(subject, int(row[0]), int(row[1]), str(row[2]))

    def schema(self, schema_id: int) -> str:
        """The schema text registered under a global ID."""
        with self._db() as db:
            row = db.execute("SELECT schema FROM schemas WHERE id = ?", (schema_id,)).fetchone()
        if not row:
            raise NotFoundError(f"schema id {schema_id} not found")
        return str(row[0])

    def subjects(self) -> list[str]:
        """All subjects, sorted."""
        with self._db() as db:
            return [str(r[0]) for r in db.execute("SELECT DISTINCT subject FROM versions ORDER BY subject")]

    def versions(self, subject: str) -> list[int]:
        """Version numbers of a subject."""
        with self._db() as db:
            history = self._history(db, check_subject(subject))
        if not history:
            raise NotFoundError(f"subject {subject!r} not found")
        return [v for v, _, _ in history]

    def version(self, subject: str, which: str) -> Version:
        """One version by number or 'latest'."""
        with self._db() as db:
            history = self._history(db, check_subject(subject))
        if not history:
            raise NotFoundError(f"subject {subject!r} not found")
        if which == "latest":
            v, sid, text = history[-1]
            return Version(subject, v, sid, text)
        if not which.isdigit():
            raise ValueError(f"version must be a positive integer or 'latest', got {which[:20]!r}")
        for v, sid, text in history:
            if v == int(which):
                return Version(subject, v, sid, text)
        raise NotFoundError(f"version {which} of subject {subject!r} not found")
