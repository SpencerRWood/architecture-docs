"""Independent processes coordinate through PostgreSQL, including commit boundaries."""

from concurrent.futures import ProcessPoolExecutor
from multiprocessing import get_context

from architecture_docs.publishing.google_drive import PublicationError
from architecture_docs.publishing.store import PublicationStore
from architecture_docs.store import SnapshotStore
from test_reconciliation import complete, node, observation


def reconcile_in_process(url: str) -> tuple[str, bool]:
    record = SnapshotStore(url).reconcile(
        complete(observation("architecture.node", node("api")))
    )
    return record.snapshot.id, record.diff.material


def publication_in_process(url: str) -> str:
    try:
        with PublicationStore(url).locked("scope"):
            return "available"
    except PublicationError as error:
        return str(error)


def test_process_writers_observe_the_committed_head(database_url: str) -> None:
    with ProcessPoolExecutor(max_workers=2, mp_context=get_context("spawn")) as pool:
        results = list(pool.map(reconcile_in_process, [database_url, database_url]))
    assert results[0][0] == results[1][0]
    assert sorted(result[1] for result in results) == [False, True]
    assert len(SnapshotStore(database_url).recent_runs()) == 2


def test_publication_lock_survives_commits_and_releases_on_close(
    database_url: str,
) -> None:
    with ProcessPoolExecutor(max_workers=1, mp_context=get_context("spawn")) as pool:
        with PublicationStore(database_url).locked("scope") as conn:
            conn.commit()
            assert pool.submit(publication_in_process, database_url).result(30) == (
                "publication_store_busy"
            )
        assert (
            pool.submit(publication_in_process, database_url).result(30) == "available"
        )
