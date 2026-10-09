"""Transactional PostgreSQL snapshot history, head and normalized run diagnostics."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from typing import Any

from psycopg.rows import dict_row

from architecture_docs.codec import snapshot_from_json
from architecture_docs.database import (
    DatabaseConnection,
    connection,
    database_url,
    lock_key,
    migrate,
)
from architecture_docs.declarations import canonical
from architecture_docs.model import CollectionResult
from architecture_docs.reconciliation import (
    DEFAULT_POLICY,
    Diff,
    Policy,
    Snapshot,
    compare,
    reconcile,
)


@dataclass(frozen=True)
class ReconciliationRecord:
    run_id: int
    snapshot: Snapshot
    diff: Diff


class SnapshotStore:
    """Atomic head/history/diff and content-addressed state under a writer lock."""

    def __init__(self, url: str | None = None) -> None:
        self._url = database_url(url)
        with self.connection() as connection:
            migrate(
                connection,
                "architecture_snapshot",
                (
                    "CREATE TABLE snapshots (id TEXT PRIMARY KEY, "
                    "payload TEXT NOT NULL)",
                    "CREATE TABLE head (singleton INTEGER PRIMARY KEY "
                    "CHECK(singleton=1), "
                    "snapshot_id TEXT NOT NULL REFERENCES snapshots(id))",
                    "CREATE TABLE runs (id BIGINT GENERATED ALWAYS AS IDENTITY "
                    "PRIMARY KEY, previous_id TEXT, snapshot_id TEXT, diff TEXT, "
                    "state TEXT NOT NULL, "
                    "reason TEXT, sources TEXT)",
                ),
            )
            # Explicit additive migration: preserve diagnostic head/history and
            # establish a separate baseline for complete architecture changes.
            connection.execute(
                "CREATE TABLE IF NOT EXISTS successful_head ("
                "singleton INTEGER PRIMARY KEY CHECK(singleton=1), "
                "snapshot_id TEXT NOT NULL REFERENCES snapshots(id))"
            )
            connection.execute(
                "INSERT INTO successful_head(singleton,snapshot_id) "
                "SELECT 1,snapshot_id FROM runs r JOIN snapshots s "
                "ON s.id=r.snapshot_id WHERE r.state='committed' "
                "AND s.payload::jsonb->'collection'->'failures'='[]'::jsonb "
                "AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements("
                "s.payload::jsonb->'evidence') e "
                "WHERE e->>'verification'<>'verified') "
                "ORDER BY r.id DESC LIMIT 1 ON CONFLICT(singleton) DO NOTHING"
            )

    @contextmanager
    def connection(self) -> Iterator[DatabaseConnection]:
        with connection(self._url, "architecture_snapshot") as conn:
            yield conn

    @staticmethod
    def load(connection: DatabaseConnection, identity: str) -> Snapshot:
        row = connection.execute(
            "SELECT payload FROM snapshots WHERE id=%s", (identity,)
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
                connection.execute(
                    "SELECT pg_advisory_xact_lock(%s)",
                    (lock_key("architecture_snapshot:writer"),),
                )
                row = connection.execute(
                    "SELECT snapshot_id FROM head WHERE singleton=1"
                ).fetchone()
                previous_id = row[0] if row else None
                previous = self.load(connection, row[0]) if row else None
                snapshot, diff = reconcile(collection, previous, policy)
                baseline_row = connection.execute(
                    "SELECT snapshot_id FROM successful_head WHERE singleton=1"
                ).fetchone()
                baseline = (
                    self.load(connection, baseline_row[0]) if baseline_row else None
                )
                diff = compare(baseline, snapshot)
                connection.execute(
                    "INSERT INTO snapshots(id,payload) VALUES (%s,%s) "
                    "ON CONFLICT(id) DO NOTHING",
                    (snapshot.id, snapshot.to_json()),
                )
                cursor = connection.execute(
                    "INSERT INTO runs(previous_id,snapshot_id,diff,state) "
                    "VALUES (%s,%s,%s,'committed') RETURNING id",
                    (diff.previous, snapshot.id, diff.to_json()),
                )
                connection.execute(
                    "INSERT INTO head VALUES(1,%s) ON CONFLICT(singleton) "
                    "DO UPDATE SET snapshot_id=excluded.snapshot_id",
                    (snapshot.id,),
                )
                if snapshot.successful:
                    connection.execute(
                        "INSERT INTO successful_head VALUES(1,%s) "
                        "ON CONFLICT(singleton) DO UPDATE SET "
                        "snapshot_id=excluded.snapshot_id",
                        (snapshot.id,),
                    )
                connection.commit()
                run = cursor.fetchone()
                if run is None:
                    raise ValueError("missing run record")
                return ReconciliationRecord(int(run[0]), snapshot, diff)
        except ValueError, TypeError, KeyError:
            # Persist only a normalized failure code, never an exception or body.
            with self.connection() as connection:
                connection.execute(
                    "INSERT INTO runs(state,reason,previous_id,sources) "
                    "VALUES ('failed','reconciliation_error',%s,%s)",
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
        with (
            self.connection() as connection,
            connection.cursor(row_factory=dict_row) as cursor,
        ):
            cursor.execute(
                "SELECT id,previous_id,snapshot_id,diff,state,reason,sources "
                "FROM runs ORDER BY id DESC LIMIT %s",
                (limit,),
            )
            return tuple(cursor.fetchall())
