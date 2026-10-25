"""The SQLite index of the store: a cache of the writers' files (#393 decision 2).

Table `events` holds the envelope as columns, `detail` as JSON and, as generated columns, each
detail field the event list names (so the list's queries run as written). `files` holds how far
each file is read; a refresh reads only what follows, up to the last complete line, and unites
events by `id`. A complete line that is no valid event lands in `bad_lines` with its code. The
schema is keyed to the closed sets: a different closed-set file rebuilds the cache.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC
from pathlib import Path

from orch_it.events import schema

BUSY_TIMEOUT_S = 60


class QueryFailed(Exception):
    pass


@dataclass(frozen=True)
class RefreshReport:
    files_read: int
    added: int
    duplicates: int
    bad_lines: int  # complete lines in the files that are no valid event, all files
    pending_bytes: int  # half-written last lines, left for the next refresh


def _detail_columns() -> list[str]:
    envelope = set(schema.envelope_fields()) | {"at_local", "detail"}
    return [name for name in schema.detail_field_names() if name not in envelope]


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _schema_sql() -> list[str]:
    envelope = []
    for name in schema.envelope_fields():
        if name == "id":
            envelope.append("id TEXT PRIMARY KEY")
        elif name == "ticket":
            envelope.append("ticket INTEGER")
        else:
            envelope.append(f"{_quote(name)} TEXT")
        if name == "at":
            envelope.append("at_local TEXT NOT NULL")
    generated = [
        f"{_quote(name)} GENERATED ALWAYS AS (json_extract(detail, {_json_path(name)})) VIRTUAL"
        for name in _detail_columns()
    ]
    columns = ",\n  ".join(envelope + ["detail TEXT NOT NULL"] + generated)
    return [
        "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
        "CREATE TABLE files (path TEXT PRIMARY KEY, read_to INTEGER NOT NULL)",
        "CREATE TABLE bad_lines (path TEXT NOT NULL, line_offset INTEGER NOT NULL,"
        " code TEXT NOT NULL, PRIMARY KEY (path, line_offset))",
        f"CREATE TABLE events (\n  {columns}\n)",
        "CREATE INDEX events_kind_at ON events (kind, at)",
        "CREATE INDEX events_session ON events (session)",
        "CREATE INDEX events_repo_ticket ON events (repo, ticket)",
    ]


def _json_path(name: str) -> str:
    return "'$.\"" + name.replace("'", "''").replace('"', '\\"') + "\"'"


def _schema_key() -> str:
    return hashlib.sha256("\n".join(_schema_sql()).encode("utf-8")).hexdigest()


def _connect(index_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(index_path, timeout=BUSY_TIMEOUT_S, isolation_level=None)
    connection.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_S * 1000}")
    return connection


def _ensure_schema(connection: sqlite3.Connection) -> None:
    key = _schema_key()
    has_meta = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'meta'").fetchone()
    if has_meta:
        row = connection.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone()
        if row and row[0] == key:
            return
    for (name,) in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'").fetchall():
        connection.execute(f"DROP TABLE IF EXISTS {_quote(name)}")
    for statement in _schema_sql():
        connection.execute(statement)
    connection.execute("INSERT INTO meta VALUES ('schema', ?)", (key,))


def utc_at(at: str) -> str:
    """`at` in UTC at a fixed width, so text order is time order."""

    return schema.parse_at(at).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _row(event: dict) -> tuple:
    values = []
    for name in schema.envelope_fields():
        values.append(utc_at(event["at"]) if name == "at" else event[name])
        if name == "at":
            values.append(event["at"])
    values.append(json.dumps(event["detail"], ensure_ascii=False, separators=(",", ":")))
    return tuple(values)


def _insert_sql() -> str:
    count = len(schema.envelope_fields()) + 2
    names = []
    for name in schema.envelope_fields():
        names.append(_quote(name))
        if name == "at":
            names.append("at_local")
    names.append("detail")
    return f"INSERT OR IGNORE INTO events ({', '.join(names)}) VALUES ({', '.join('?' * count)})"


def _parse(raw: bytes) -> dict | str:
    """The event of one complete line, or the code of why it is none."""

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return "bad_utf8"
    try:
        event = json.loads(text)
    except (json.JSONDecodeError, RecursionError):
        return "bad_json"
    try:
        schema.validate(event)
    except schema.EventError as e:
        return e.code
    return event


def refresh(root: Path, index_path: Path) -> RefreshReport:
    connection = _connect(index_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        try:
            _ensure_schema(connection)
            report = _read_new_lines(root, connection)
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise
    finally:
        connection.close()
    return report


def _read_new_lines(root: Path, connection: sqlite3.Connection) -> RefreshReport:
    read_to = dict(connection.execute("SELECT path, read_to FROM files").fetchall())
    insert = _insert_sql()
    files_read = added = duplicates = pending = 0
    for path in sorted(root.glob("*/*.jsonl")):
        name = path.relative_to(root).as_posix()
        start = read_to.get(name, 0)
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            continue
        if size < start:
            start = 0  # the file was replaced: read it again; ids keep it from counting twice
        if size == start:
            continue
        files_read += 1
        offset = start
        with path.open("rb") as f:
            f.seek(start)
            while True:
                raw = f.readline()
                if not raw:
                    break
                if not raw.endswith(b"\n"):
                    pending += len(raw)
                    break
                line_offset, offset = offset, offset + len(raw)
                if not raw.strip():
                    continue
                parsed = _parse(raw.rstrip(b"\r\n"))
                if isinstance(parsed, str):
                    connection.execute("INSERT OR REPLACE INTO bad_lines VALUES (?, ?, ?)",
                                       (name, line_offset, parsed))
                    continue
                if connection.execute(insert, _row(parsed)).rowcount:
                    added += 1
                else:
                    duplicates += 1
        connection.execute("INSERT OR REPLACE INTO files VALUES (?, ?)", (name, offset))
    bad_lines = connection.execute("SELECT count(*) FROM bad_lines").fetchone()[0]
    return RefreshReport(files_read, added, duplicates, bad_lines, pending)


def query(index_path: Path, sql: str, params: dict | tuple = ()) -> tuple[list[str], list[tuple]]:
    connection = _connect(index_path)
    try:
        connection.execute("PRAGMA query_only = ON")
        try:
            cursor = connection.execute(sql, params)
            rows = cursor.fetchall()
        except (sqlite3.Error, sqlite3.Warning) as e:
            raise QueryFailed(str(e)) from None
        columns = [d[0] for d in cursor.description] if cursor.description else []
        return columns, rows
    finally:
        connection.close()


def existing_ids(index_path: Path, ids: list[str]) -> set[str]:
    connection = _connect(index_path)
    try:
        connection.execute("CREATE TEMP TABLE wanted (id TEXT PRIMARY KEY)")
        connection.executemany("INSERT OR IGNORE INTO wanted VALUES (?)", ((i,) for i in ids))
        found = connection.execute("SELECT id FROM events JOIN wanted USING (id)").fetchall()
        return {row[0] for row in found}
    finally:
        connection.close()
