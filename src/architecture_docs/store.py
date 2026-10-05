"""Transactional SQLite snapshot ledger at an explicit persistent filesystem path."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from architecture_docs.codec import snapshot_from_json
from architecture_docs.declarations import canonical
from architecture_docs.model import CollectionResult
from architecture_docs.reconciliation import (
    DEFAULT_POLICY,
    Diff,
    Policy,
    Snapshot,
    reconcile,
)


@dataclass(frozen=True)
class ReconciliationRecord:
    run_id: int
    snapshot: Snapshot
    diff: Diff


class SnapshotStore:
    """Atomic head/history/diff and content-addressed state under a writer lock."""

    def __init__(self, path: Path) -> None:
        if str(path) == ":memory:":
            raise ValueError("persistent snapshot path required")
        self.path = path.resolve()
        with self.connection() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in {0, 1}:
                raise ValueError("unsupported store schema")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS snapshots (
                    id TEXT PRIMARY KEY, payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS head (
                    singleton INTEGER PRIMARY KEY CHECK (singleton=1),
                    snapshot_id TEXT NOT NULL REFERENCES snapshots(id)
                );
                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY, previous_id TEXT, snapshot_id TEXT,
                    diff TEXT, state TEXT NOT NULL, reason TEXT, sources TEXT
                );
                PRAGMA user_version=1;
                """
            )

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = None
        try:
            connection = sqlite3.connect(self.path, timeout=30)
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA synchronous=FULL")
            yield connection
        except sqlite3.Error:
            raise ValueError("snapshot store unavailable") from None
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def load(connection: sqlite3.Connection, identity: str) -> Snapshot:
        row = connection.execute(
            "SELECT payload FROM snapshots WHERE id=?", (identity,)
        ).fetchone()
        if row is None:
            raise ValueError("missing snapshot record")
        snapshot = snapshot_from_json(row[0])
        if snapshot.id != identity:
            raise ValueError("snapshot hash mismatch")
        return snapshot

    def latest(self) -> Snapshot | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT snapshot_id FROM head WHERE singleton=1"
            ).fetchone()
            return self.load(connection, row[0]) if row else None

    def get(self, identity: str) -> Snapshot:
        with self.connection() as connection:
            return self.load(connection, identity)

    def reconcile(
        self, collection: CollectionResult, policy: Policy = DEFAULT_POLICY
    ) -> ReconciliationRecord:
        previous_id = None
        try:
            with self.connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT snapshot_id FROM head WHERE singleton=1"
                ).fetchone()
                previous_id = row[0] if row else None
                previous = self.load(connection, row[0]) if row else None
                snapshot, diff = reconcile(collection, previous, policy)
                connection.execute(
                    "INSERT OR IGNORE INTO snapshots(id,payload) VALUES (?,?)",
                    (snapshot.id, snapshot.to_json()),
                )
                cursor = connection.execute(
                    "INSERT INTO runs(previous_id,snapshot_id,diff,state) "
                    "VALUES (?,?,?,'committed')",
                    (diff.previous, snapshot.id, diff.to_json()),
                )
                connection.execute(
                    "INSERT INTO head VALUES(1,?) ON CONFLICT(singleton) "
                    "DO UPDATE SET snapshot_id=excluded.snapshot_id",
                    (snapshot.id,),
                )
                connection.commit()
                return ReconciliationRecord(int(cursor.lastrowid or 0), snapshot, diff)
        except ValueError, TypeError, KeyError:
            # Persist only a normalized failure code, never an exception or body.
            with self.connection() as connection:
                connection.execute(
                    "INSERT INTO runs(state,reason,previous_id,sources) "
                    "VALUES ('failed','reconciliation_error',?,?)",
                    (
                        previous_id,
                        canonical(
                            [
                                asdict(provenance)
                                for provenance in sorted(
                                    {
                                        item.provenance
                                        for item in collection.observations
                                    },
                                    key=lambda item: canonical(asdict(item)),
                                )
                            ]
                        ),
                    ),
                )
                connection.commit()
            raise ValueError("reconciliation failed; inspect ledger") from None

    def recent_runs(self, limit: int = 20) -> tuple[dict[str, Any], ...]:
        if not 1 <= limit <= 100:
            raise ValueError("invalid diagnostic limit")
        with self.connection() as connection:
            connection.row_factory = sqlite3.Row
            return tuple(
                dict(row)
                for row in connection.execute(
                    "SELECT id,previous_id,snapshot_id,diff,state,reason,sources "
                    "FROM runs ORDER BY id DESC LIMIT ?",
                    (limit,),
                )
            )
