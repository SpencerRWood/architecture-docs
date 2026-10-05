"""Durable restart, rollback, serialization, and bounded manual diagnosis."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import pytest

from architecture_docs.model import CollectionResult, Failure
from architecture_docs.store import SnapshotStore
from test_reconciliation import complete, node, observation


def test_restart_snapshot_deduplication_history_and_transition_diffs(
    tmp_path: Path,
) -> None:
    path = tmp_path / "snapshots.sqlite3"
    store = SnapshotStore(path)
    assert store.latest() is None
    collection = complete(observation("architecture.node", node("api", host="linux")))
    first = store.reconcile(collection)
    assert first.diff.previous is None
    reopened = SnapshotStore(path)
    assert reopened.latest() == first.snapshot
    repeated = reopened.reconcile(collection)
    assert repeated.snapshot.id == first.snapshot.id
    assert not repeated.diff.material
    changed = reopened.reconcile(
        complete(observation("architecture.node", node("api", host="nas")))
    )
    reverted = reopened.reconcile(collection)
    assert reverted.snapshot.id == first.snapshot.id
    assert reverted.diff.previous == changed.snapshot.id
    assert reverted.diff.material
    assert reopened.get(changed.snapshot.id) == changed.snapshot
    runs = reopened.recent_runs(2)
    assert len(runs) == 2
    assert runs[0]["id"] == reverted.run_id
    assert json.loads(runs[0]["diff"])["previous"] == changed.snapshot.id
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 2


def test_partial_failure_is_persisted_and_recovery_uses_known_good_facts(
    tmp_path: Path,
) -> None:
    path = tmp_path / "snapshots.sqlite3"
    original = observation("architecture.node", node("api", host="linux"))
    first = SnapshotStore(path).reconcile(complete(original))
    partial = SnapshotStore(path).reconcile(
        CollectionResult(
            failures=(Failure("fixture", original.provenance, "parse_error"),)
        )
    )
    assert partial.snapshot.publication_blocked
    assert partial.snapshot.evidence[0].observation == original
    recovered = SnapshotStore(path).reconcile(complete(original))
    assert recovered.snapshot.id == first.snapshot.id
    assert not recovered.diff.material
    assert SnapshotStore(path).get(partial.snapshot.id).collection.failures


def test_normalization_failure_does_not_advance_head_and_is_diagnosable(
    tmp_path: Path,
) -> None:
    store = SnapshotStore(tmp_path / "snapshots.sqlite3")
    first = store.reconcile(complete(observation("architecture.node", node("api"))))
    invalid = complete(observation("architecture.node", "fixture-sensitive-literal"))
    with pytest.raises(ValueError, match="reconciliation failed; inspect ledger"):
        store.reconcile(invalid)
    assert store.latest() == first.snapshot
    diagnosis = store.recent_runs()[0]
    assert diagnosis["state"] == "failed"
    assert diagnosis["reason"] == "reconciliation_error"
    assert "fixture-sensitive-literal" not in json.dumps(diagnosis)
    assert diagnosis["snapshot_id"] is None


def test_transaction_write_failure_rolls_back_snapshot_and_head(tmp_path: Path) -> None:
    path = tmp_path / "snapshots.sqlite3"
    store = SnapshotStore(path)
    first = store.reconcile(complete(observation("architecture.node", node("api"))))
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(
            "CREATE TRIGGER reject_head BEFORE UPDATE ON head BEGIN "
            "SELECT RAISE(ABORT, 'fixture-sensitive-literal'); END;"
        )
        connection.commit()
    with pytest.raises(ValueError, match="reconciliation failed; inspect ledger"):
        store.reconcile(complete(observation("architecture.node", node("new"))))
    assert store.latest() == first.snapshot
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 1
    assert store.recent_runs()[0]["state"] == "failed"


def test_competing_writers_compare_against_committed_head(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path / "snapshots.sqlite3")
    collection = complete(observation("architecture.node", node("api")))
    with ThreadPoolExecutor(max_workers=2) as executor:
        records = list(executor.map(lambda _: store.reconcile(collection), range(2)))
    assert records[0].snapshot.id == records[1].snapshot.id
    assert sorted(record.diff.material for record in records) == [False, True]
    assert len(store.recent_runs()) == 2


def test_bad_store_schema_missing_records_and_corruption_fail_closed(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="persistent snapshot path"):
        SnapshotStore(Path(":memory:"))
    with pytest.raises(ValueError, match="snapshot store unavailable"):
        SnapshotStore(tmp_path / "missing" / "snapshot.sqlite3")
    path = tmp_path / "snapshots.sqlite3"
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA user_version=99")
    with pytest.raises(ValueError, match="unsupported store schema"):
        SnapshotStore(path)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA user_version=0")
    store = SnapshotStore(path)
    with pytest.raises(ValueError, match="missing snapshot record"):
        store.get("missing")
    for limit in (0, 101):
        with pytest.raises(ValueError, match="invalid diagnostic limit"):
            store.recent_runs(limit)
    first = store.reconcile(complete(observation("architecture.node", node("api"))))
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(
            "UPDATE snapshots SET payload=? WHERE id=?",
            (replace(first.snapshot, schema_version=2).to_json(), first.snapshot.id),
        )
        connection.commit()
    with pytest.raises(ValueError, match="invalid snapshot record"):
        store.latest()


def test_hash_mismatch_never_becomes_latest_successful_evidence(tmp_path: Path) -> None:
    path = tmp_path / "snapshots.sqlite3"
    store = SnapshotStore(path)
    first = store.reconcile(complete(observation("architecture.node", node("api"))))
    second = store.reconcile(complete(observation("architecture.node", node("new"))))
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(
            "UPDATE snapshots SET payload=? WHERE id=?",
            (first.snapshot.to_json(), second.snapshot.id),
        )
        connection.commit()
    with pytest.raises(ValueError, match="snapshot hash mismatch"):
        store.latest()
