"""One-time, explicit read-only SQLite import; never a runtime storage fallback."""

import argparse
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from typing import Any

from psycopg import sql

from architecture_docs.codec import snapshot_from_json
from architecture_docs.database import database_url, lock_key
from architecture_docs.publishing.google_drive import PublicationError
from architecture_docs.publishing.store import PublicationStore
from architecture_docs.store import SnapshotStore

SNAPSHOT_TABLES = {
    "snapshots": ("id", "payload"),
    "head": ("singleton", "snapshot_id"),
    "runs": ("id", "previous_id", "snapshot_id", "diff", "state", "reason", "sources"),
}
PUBLICATION_TABLES = {
    "identities": ("key", "file_id", "attempted", "committed", "pending"),
    "events": ("id", "snapshot_id", "artifact", "state", "reason", "file_id"),
}


def read_ledger(path: Path, tables: dict[str, tuple[str, ...]]) -> dict[str, list[Any]]:
    with closing(
        sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    ) as conn:
        if conn.execute("PRAGMA user_version").fetchone() != (1,):
            raise ValueError("unsupported import schema")
        return {
            table: conn.execute(
                "SELECT " + ",".join(columns) + " FROM " + table  # noqa: S608 -- constants
            ).fetchall()
            for table, columns in tables.items()
        }


def import_ledgers(
    snapshots: Path,
    publication: Path,
    url: str | None = None,
) -> dict[str, int]:
    """Quiesced sources, empty target, preserved identities, one atomic data import."""
    url = database_url(url)
    history = read_ledger(snapshots, SNAPSHOT_TABLES)
    ledger = read_ledger(
        publication, {"configuration": ("scope",), **PUBLICATION_TABLES}
    )
    if len(ledger["configuration"]) != 1:
        raise ValueError("import requires one configured publication scope")
    for identity, payload in history["snapshots"]:
        if snapshot_from_json(payload).id != identity:
            raise ValueError("snapshot import hash mismatch")
    for _, _, _, committed, pending in ledger["identities"]:
        for state in (committed, pending):
            if state is not None and not isinstance(json.loads(state), dict):
                raise ValueError("invalid publication import state")
    SnapshotStore(url)
    counts = {}
    with PublicationStore(url).locked(ledger["configuration"][0][0]) as conn:
        conn.execute(
            "SELECT pg_advisory_xact_lock(%s)",
            (lock_key("architecture_snapshot:writer"),),
        )
        for schema, tables, source in (
            ("architecture_snapshot", SNAPSHOT_TABLES, history),
            ("architecture_publication", PUBLICATION_TABLES, ledger),
        ):
            for table, columns in tables.items():
                qualified = sql.Identifier(schema, table)
                if conn.execute(
                    sql.SQL("SELECT 1 FROM {} LIMIT 1").format(qualified)
                ).fetchone():
                    raise ValueError("import target must be empty")
                statement = sql.SQL(
                    "INSERT INTO {} ({}) OVERRIDING SYSTEM VALUE VALUES ({})"
                ).format(
                    qualified,
                    sql.SQL(",").join(map(sql.Identifier, columns)),
                    sql.SQL(",").join(sql.Placeholder() for _ in columns),
                )
                with conn.cursor() as cursor:
                    cursor.executemany(statement, source[table])
                counts[schema + "." + table] = len(source[table])
            # Explicit imported IDs must not collide with subsequent generated IDs.
            sequenced = "runs" if schema == "architecture_snapshot" else "events"
            conn.execute(
                sql.SQL(
                    "SELECT setval(pg_get_serial_sequence(%s,'id'), "
                    "COALESCE(MAX(id),1), MAX(id) IS NOT NULL) FROM {}"
                ).format(sql.Identifier(schema, sequenced)),
                (schema + "." + sequenced,),
            )
    return counts


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshots", required=True, type=Path)
    parser.add_argument("--publication", required=True, type=Path)
    args = parser.parse_args(arguments)
    try:
        counts = import_ledgers(args.snapshots, args.publication)
    except ValueError, OSError, sqlite3.Error, PublicationError:
        parser.exit(
            2,
            "Ledger import failed; preserve source backups and inspect target state.\n",
        )
    sys.stdout.write(json.dumps({"imported_rows": counts}) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
