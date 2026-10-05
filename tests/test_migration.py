"""Explicit legacy import preserves history and remote identities atomically."""

import json
import sqlite3
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

import pytest

from architecture_docs.database import connection
from architecture_docs.database import database_url as resolve_database_url
from architecture_docs.migration import (
    PUBLICATION_TABLES,
    SNAPSHOT_TABLES,
    import_ledgers,
    main,
)
from architecture_docs.publishing import publish
from architecture_docs.publishing.store import PublicationStore
from architecture_docs.store import SnapshotStore
from test_publication import DriveService
from test_rendering import representative


def export_fixture(url: str, directory: Path) -> tuple[Path, Path]:
    paths = []
    for schema, tables in (
        ("architecture_snapshot", SNAPSHOT_TABLES),
        (
            "architecture_publication",
            {"configuration": ("scope",), **PUBLICATION_TABLES},
        ),
    ):
        path = directory / (schema + ".sqlite3")
        paths.append(path)
        with (
            connection(url, schema) as source,
            closing(sqlite3.connect(path)) as target,
        ):
            target.execute("PRAGMA user_version=1")
            for table, columns in tables.items():
                target.execute("CREATE TABLE " + table + " (" + ",".join(columns) + ")")
                rows = source.execute(
                    "SELECT " + ",".join(columns) + " FROM " + table  # noqa: S608
                ).fetchall()
                target.executemany(
                    "INSERT INTO "
                    + table
                    + " VALUES("
                    + ",".join("?" for _ in columns)
                    + ")",
                    rows,
                )
            target.commit()
    return paths[0], paths[1]


def test_import_preserves_ids_pending_state_history_and_sequences(
    tmp_path: Path,
    database_url: str,
    database_factory: Callable[[], str],
) -> None:
    store = SnapshotStore(database_url)
    snapshot = store.reconcile(representative().collection).snapshot
    service = DriveService()
    service.update_failure = "after"
    first = publish(snapshot, service.client(), database_url, "parent")
    assert not first.complete
    snapshots, publication = export_fixture(database_url, tmp_path)
    before = (snapshots.read_bytes(), publication.read_bytes())
    destination = database_factory()
    counts = import_ledgers(snapshots, publication, destination)
    assert counts["architecture_snapshot.snapshots"] == 1
    assert counts["architecture_publication.identities"] == 17
    imported = SnapshotStore(destination)
    assert imported.latest() == snapshot
    assert imported.recent_runs()[0]["id"] == 1
    repeated = publish(snapshot, service.client(), destination, "parent")
    assert repeated.complete
    assert repeated.artifacts[0].state == "recovered"
    assert len(service.files) == 17
    assert len(service.writes) == 15
    assert imported.reconcile(snapshot.collection).run_id == 2
    with connection(destination, "architecture_publication") as conn:
        assert conn.execute("SELECT MAX(id) FROM events").fetchone() == (30,)
    assert (snapshots.read_bytes(), publication.read_bytes()) == before
    with pytest.raises(ValueError, match="target must be empty"):
        import_ledgers(snapshots, publication, destination)


def test_import_rolls_back_on_nonempty_publication_target(
    tmp_path: Path,
    database_url: str,
    database_factory: Callable[[], str],
) -> None:
    snapshot = (
        SnapshotStore(database_url).reconcile(representative().collection).snapshot
    )
    publish(snapshot, DriveService().client(), database_url, "parent")
    snapshots, publication = export_fixture(database_url, tmp_path)
    destination = database_factory()
    with connection(database_url, "architecture_publication") as source:
        row = source.execute("SELECT scope FROM configuration").fetchone()
        assert row is not None
    with PublicationStore(destination).locked(row[0]) as conn:
        conn.execute("INSERT INTO identities VALUES('existing',NULL,0,NULL,NULL)")
    with pytest.raises(ValueError, match="target must be empty"):
        import_ledgers(snapshots, publication, destination)
    assert SnapshotStore(destination).latest() is None
    with connection(destination, "architecture_snapshot") as conn:
        assert conn.execute("SELECT COUNT(*) FROM snapshots").fetchone() == (0,)


def test_missing_configuration_and_bad_snapshot_reject_import(
    tmp_path: Path,
    database_url: str,
    database_factory: Callable[[], str],
) -> None:
    snapshot = (
        SnapshotStore(database_url).reconcile(representative().collection).snapshot
    )
    publish(snapshot, DriveService().client(), database_url, "parent")
    snapshots, publication = export_fixture(database_url, tmp_path)
    with closing(sqlite3.connect(snapshots)) as conn:
        conn.execute("UPDATE snapshots SET id='wrong'")
        conn.commit()
    with pytest.raises(ValueError, match="hash mismatch"):
        import_ledgers(snapshots, publication, database_factory())
    with closing(sqlite3.connect(publication)) as conn:
        conn.execute("DELETE FROM configuration")
        conn.commit()
    with pytest.raises(ValueError, match="configured publication scope"):
        import_ledgers(snapshots, publication, database_factory())


def test_cli_import_and_missing_configuration_are_sanitized(
    tmp_path: Path,
    database_url: str,
    database_factory: Callable[[], str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("ARCHITECTURE_DOCS_DATABASE_URL", raising=False)
    with pytest.raises(ValueError, match="ARCHITECTURE_DOCS_DATABASE_URL"):
        resolve_database_url()
    snapshot = (
        SnapshotStore(database_url).reconcile(representative().collection).snapshot
    )
    publish(snapshot, DriveService().client(), database_url, "parent")
    snapshots, publication = export_fixture(database_url, tmp_path)
    monkeypatch.setenv("ARCHITECTURE_DOCS_DATABASE_URL", database_factory())
    args = ["--snapshots", str(snapshots), "--publication", str(publication)]
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)["imported_rows"]
    with pytest.raises(SystemExit) as error:
        main(args)
    assert error.value.code == 2
    assert "fixture-password" not in capsys.readouterr().err


@pytest.mark.parametrize(
    "value",
    [
        "",
        "sqlite:///state",
        "postgresql://",
        "postgresql:///local_database",
        "postgresql://user:password@127.0.0.1:notaport/database",
        "postgresql://user:password@127.0.0.1/database\n",
    ],
)
def test_database_url_rejects_missing_or_implicit_connection_fields(value: str) -> None:
    with pytest.raises(ValueError, match="PostgreSQL URL") as error:
        resolve_database_url(value)
    assert value not in str(error.value) or value == ""
