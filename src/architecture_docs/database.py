"""PostgreSQL connections and application-owned, versioned schema initialization."""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from hashlib import sha256
from typing import Any
from urllib.parse import urlsplit

import psycopg
from psycopg import sql

type DatabaseConnection = psycopg.Connection[tuple[Any, ...]]


def database_url(value: str | None = None) -> str:
    value = (
        value if value is not None else os.environ.get("ARCHITECTURE_DOCS_DATABASE_URL")
    )
    try:
        if not isinstance(value, str) or any(ord(char) < 32 for char in value):
            raise ValueError
        parsed = urlsplit(value)
        if (
            parsed.scheme != "postgresql"
            or not parsed.hostname
            or not parsed.username
            or not parsed.password
            or not parsed.path.strip("/")
            or parsed.fragment
        ):
            raise ValueError
        _ = parsed.port  # Reject malformed ports before handing the URL to libpq.
    except ValueError:
        raise ValueError(
            "ARCHITECTURE_DOCS_DATABASE_URL must be a PostgreSQL URL"
        ) from None
    return value


def lock_key(name: str) -> int:
    return int.from_bytes(sha256(name.encode()).digest()[:8], signed=True)


@contextmanager
def connection(url: str, schema: str) -> Iterator[DatabaseConnection]:
    try:
        with psycopg.connect(
            url,
            connect_timeout=5,
            options="-c statement_timeout=30000 -c lock_timeout=30000",
        ) as conn:
            conn.execute(
                sql.SQL("SET search_path TO {}").format(sql.Identifier(schema))
            )
            yield conn
    except psycopg.Error:
        raise ValueError("PostgreSQL store unavailable") from None


def migrate(conn: DatabaseConnection, schema: str, statements: tuple[str, ...]) -> None:
    """Serialize initialization and refuse unknown versions; DDL is transactional."""
    conn.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key(schema + ":migration"),))
    conn.execute(
        sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema))
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version ("
        "singleton INTEGER PRIMARY KEY CHECK(singleton=1), version INTEGER NOT NULL)"
    )
    row = conn.execute(
        "SELECT version FROM schema_version WHERE singleton=1"
    ).fetchone()
    if row is not None:
        if row[0] != 1:
            raise ValueError("unsupported store schema")
        return
    for statement in statements:
        conn.execute(statement)
    conn.execute("INSERT INTO schema_version VALUES(1,1)")
