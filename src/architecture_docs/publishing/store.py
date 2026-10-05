"""Durable publication identities/intents, separate from the architecture history."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from architecture_docs.database import (
    DatabaseConnection,
    connection,
    database_url,
    lock_key,
    migrate,
)
from architecture_docs.declarations import canonical
from architecture_docs.publishing.google_drive import PublicationError


class PublicationStore:
    def __init__(self, url: str | None = None) -> None:
        try:
            self._url = database_url(url)
        except ValueError:
            raise PublicationError("missing_or_invalid_database_url") from None

    @contextmanager
    def locked(self, scope: str) -> Iterator[DatabaseConnection]:
        """Session lock survives commits; connection closure always releases it."""
        try:
            with connection(self._url, "architecture_publication") as conn:
                acquired = conn.execute(
                    "SELECT pg_try_advisory_lock(%s)",
                    (lock_key("architecture_publication:writer"),),
                ).fetchone()
                if not acquired or not acquired[0]:
                    raise PublicationError("publication_store_busy")
                migrate(
                    conn,
                    "architecture_publication",
                    (
                        "CREATE TABLE configuration (scope TEXT PRIMARY KEY)",
                        "CREATE TABLE identities (key TEXT PRIMARY KEY, file_id TEXT, "
                        "attempted INTEGER NOT NULL, committed TEXT, pending TEXT)",
                        "CREATE TABLE events (id BIGINT GENERATED ALWAYS AS IDENTITY "
                        "PRIMARY KEY, snapshot_id TEXT NOT NULL, "
                        "artifact TEXT NOT NULL, "
                        "state TEXT NOT NULL, reason TEXT, file_id TEXT)",
                    ),
                )
                rows = conn.execute("SELECT scope FROM configuration").fetchall()
                if rows and rows != [(scope,)]:
                    raise PublicationError("publication_scope_mismatch")
                conn.execute(
                    "INSERT INTO configuration VALUES (%s) ON CONFLICT DO NOTHING",
                    (scope,),
                )
                conn.commit()
                yield conn
        except ValueError as error:
            if str(error) not in {
                "unsupported store schema",
                "PostgreSQL store unavailable",
            }:
                raise
            reason = (
                "unsupported_publication_schema"
                if str(error) == "unsupported store schema"
                else "publication_store_unavailable"
            )
            raise PublicationError(reason) from None


def record(connection: DatabaseConnection, key: str) -> dict[str, Any]:
    connection.execute(
        "INSERT INTO identities VALUES (%s,NULL,0,NULL,NULL) ON CONFLICT DO NOTHING",
        (key,),
    )
    connection.commit()
    row = connection.execute(
        "SELECT file_id,attempted,committed,pending FROM identities WHERE key=%s",
        (key,),
    ).fetchone()
    if row is None:
        raise PublicationError("missing_publication_identity")
    return {
        "file_id": row[0],
        "attempted": bool(row[1]),
        "committed": json.loads(row[2]) if row[2] else None,
        "pending": json.loads(row[3]) if row[3] else None,
    }


def save_state(
    connection: DatabaseConnection, key: str, column: str, data: dict[str, object]
) -> None:
    if column not in {"pending", "committed"}:
        raise PublicationError("invalid_state_field")
    if column == "pending":
        connection.execute(
            "UPDATE identities SET pending=%s WHERE key=%s", (canonical(data), key)
        )
    else:
        connection.execute(
            "UPDATE identities SET committed=%s,pending=NULL WHERE key=%s",
            (canonical(data), key),
        )
    connection.commit()
