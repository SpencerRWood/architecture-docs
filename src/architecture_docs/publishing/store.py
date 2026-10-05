"""Durable publication identities/intents, separate from the architecture history."""

import fcntl
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from architecture_docs.declarations import canonical
from architecture_docs.publishing.google_drive import PublicationError


class PublicationStore:
    def __init__(self, path: Path) -> None:
        if str(path) == ":memory:" or not path.is_absolute():
            raise PublicationError("persistent_absolute_path_required")
        self.path = path

    @contextmanager
    def locked(self, scope: str) -> Iterator[sqlite3.Connection]:
        """One publisher per durable ledger, across commits and remote requests."""
        connection = None
        try:
            with self.path.with_suffix(self.path.suffix + ".lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                connection = sqlite3.connect(self.path)
                connection.execute("PRAGMA synchronous=FULL")
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                if version not in {0, 1}:
                    raise PublicationError("unsupported_publication_schema")
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS configuration (scope TEXT PRIMARY KEY);
                    CREATE TABLE IF NOT EXISTS identities (
                        key TEXT PRIMARY KEY, file_id TEXT, attempted INTEGER NOT NULL,
                        committed TEXT, pending TEXT
                    );
                    CREATE TABLE IF NOT EXISTS events (
                        id INTEGER PRIMARY KEY, snapshot_id TEXT NOT NULL,
                        artifact TEXT NOT NULL, state TEXT NOT NULL, reason TEXT,
                        file_id TEXT
                    );
                    PRAGMA user_version=1;
                    """
                )
                rows = connection.execute("SELECT scope FROM configuration").fetchall()
                if rows and rows != [(scope,)]:
                    raise PublicationError("publication_scope_mismatch")
                connection.execute(
                    "INSERT OR IGNORE INTO configuration VALUES (?)", (scope,)
                )
                connection.commit()
                yield connection
        except OSError, sqlite3.Error:
            raise PublicationError("publication_store_unavailable_or_busy") from None
        finally:
            if connection is not None:
                connection.close()


def record(connection: sqlite3.Connection, key: str) -> dict[str, Any]:
    connection.execute(
        "INSERT OR IGNORE INTO identities VALUES (?,NULL,0,NULL,NULL)", (key,)
    )
    connection.commit()
    row = connection.execute(
        "SELECT file_id,attempted,committed,pending FROM identities WHERE key=?", (key,)
    ).fetchone()
    return {
        "file_id": row[0],
        "attempted": bool(row[1]),
        "committed": json.loads(row[2]) if row[2] else None,
        "pending": json.loads(row[3]) if row[3] else None,
    }


def save_state(
    connection: sqlite3.Connection, key: str, column: str, data: dict[str, object]
) -> None:
    if column not in {"pending", "committed"}:
        raise PublicationError("invalid_state_field")
    if column == "pending":
        connection.execute(
            "UPDATE identities SET pending=? WHERE key=?", (canonical(data), key)
        )
    else:
        connection.execute(
            "UPDATE identities SET committed=?,pending=NULL WHERE key=?",
            (canonical(data), key),
        )
    connection.commit()
