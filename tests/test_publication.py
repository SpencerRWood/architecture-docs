"""Offline Drive/Docs service exercises identity, atomic writes and crash recovery."""

import json
import re
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest

from architecture_docs.database import connection as database_connection
from architecture_docs.estate import ExpectedRepository, contract_from_data
from architecture_docs.model import Failure
from architecture_docs.publishing import publish
from architecture_docs.publishing.__main__ import main
from architecture_docs.publishing.google_drive import (
    DOCUMENT,
    GoogleDrive,
    PublicationError,
    document_text,
    identity,
)
from architecture_docs.publishing.store import PublicationStore, save_state
from architecture_docs.reconciliation import Snapshot, reconcile
from architecture_docs.renderers import render_documents
from architecture_docs.renderers.artifacts import DocumentKind, RenderConfig
from test_reconciliation import complete, node, observation
from test_rendering import representative


def partial(snapshot: Snapshot) -> Snapshot:
    return replace(
        snapshot,
        collection=replace(
            snapshot.collection,
            failures=(
                Failure(
                    "fixture",
                    snapshot.collection.observations[0].provenance,
                    "unavailable",
                ),
            ),
        ),
    )


class DriveService:
    def __init__(self) -> None:
        self.files: dict[str, dict[str, Any]] = {}
        self.texts: dict[str, str] = {}
        self.revisions: dict[str, int] = {}
        self.writes: list[str] = []
        self.requests: list[httpx.Request] = []
        self.create_failure: str | None = None
        self.update_failure: str | None = None
        self.list_failure = False

    def client(self) -> GoogleDrive:
        return GoogleDrive("fixture-token", transport=httpx.MockTransport(self.handle))

    def handle(self, request: httpx.Request) -> httpx.Response:  # noqa: PLR0911
        self.requests.append(request)
        path = request.url.path
        if request.url.host == "www.googleapis.com":
            if path == "/drive/v3/files" and request.method == "GET":
                if self.list_failure:
                    return httpx.Response(
                        200, json={"files": [], "nextPageToken": "more"}
                    )
                query = request.url.params["q"]
                parent, scope, key = re.findall(r"'([^']+)'", query)[::2]
                files = [
                    item
                    for item in self.files.values()
                    if item["parents"] == [parent]
                    and item["appProperties"]
                    == {"architecture_scope": scope, "architecture_artifact": key}
                    and not item["trashed"]
                ]
                return httpx.Response(200, json={"files": files})
            if request.method == "POST":
                if self.create_failure == "before":
                    self.create_failure = None
                    raise httpx.ReadTimeout("fixture-sensitive-literal")
                item = json.loads(request.content)
                file_id = f"file-{len(self.files) + 1}"
                item.update(id=file_id, trashed=False)
                self.files[file_id] = item
                self.texts[file_id] = ""
                self.revisions[file_id] = 1
                if self.create_failure == "after":
                    self.create_failure = None
                    raise httpx.ReadTimeout("fixture-sensitive-literal")
                return httpx.Response(200, json=item)
            file_id = path.rsplit("/", 1)[-1]
            return httpx.Response(200, json=self.files[file_id])
        file_id = path.rsplit("/", 1)[-1].removesuffix(":batchUpdate")
        if request.method == "GET":
            value = self.texts[file_id] + "\n"
            return httpx.Response(
                200,
                json={
                    "documentId": file_id,
                    "revisionId": str(self.revisions[file_id]),
                    "body": {
                        "content": [
                            {"sectionBreak": {}, "endIndex": 1},
                            {
                                "startIndex": 1,
                                "endIndex": 1 + len(value.encode("utf-16-le")) // 2,
                                "paragraph": {
                                    "elements": [{"textRun": {"content": value}}]
                                },
                            },
                        ]
                    },
                },
            )
        data = json.loads(request.content)
        revision = data["writeControl"]["requiredRevisionId"]
        if self.update_failure == "revision" or revision != str(
            self.revisions[file_id]
        ):
            self.update_failure = None
            return httpx.Response(400, json={"error": "fixture-sensitive-literal"})
        requests = data["requests"]
        if self.texts[file_id]:
            assert requests[0]["deleteContentRange"]["range"] == {
                "startIndex": 1,
                "endIndex": 1 + len(self.texts[file_id].encode("utf-16-le")) // 2,
            }
        self.texts[file_id] = requests[-1]["insertText"]["text"]
        self.revisions[file_id] += 1
        self.writes.append(file_id)
        if self.update_failure == "after":
            self.update_failure = None
            raise httpx.ReadTimeout("fixture-sensitive-literal")
        if self.update_failure == "corrupt":
            self.update_failure = None
            self.texts[file_id] += "collaborator edit"
        return httpx.Response(200, json={})


def test_create_native_hierarchy_restart_and_provenance_no_change(
    database_url: str,
) -> None:
    service = DriveService()
    drive = service.client()
    snapshot = representative()
    state = database_url
    first = publish(snapshot, drive, state, "approved-parent")
    assert first.complete
    assert len(service.files) == 17
    assert len(service.writes) == 15
    assert all(item.state == "published" for item in first.artifacts)
    files = tuple(item.file_id for item in first.artifacts)
    architecture = next(
        item for item in service.files.values() if item["name"] == "Architecture"
    )
    runbooks = next(
        item for item in service.files.values() if item["name"] == "Runbooks"
    )
    assert architecture["parents"] == ["approved-parent"]
    assert runbooks["parents"] == [architecture["id"]]
    for item in service.files.values():
        if item["mimeType"] == DOCUMENT:
            key = item["appProperties"]["architecture_artifact"]
            assert item["parents"] == [
                runbooks["id"] if key.startswith("runbook-") else architecture["id"]
            ]
            assert snapshot.id not in service.texts[item["id"]]
    with database_connection(state, "architecture_publication") as db:
        committed = db.execute(
            "SELECT committed FROM identities WHERE committed IS NOT NULL"
        ).fetchall()
    assert all(json.loads(row[0])["snapshot_id"] == snapshot.id for row in committed)
    drive.close()
    # New client/process, same durable identities. Provenance-only churn is skipped.
    updated = replace(
        snapshot,
        collection=replace(
            snapshot.collection,
            observations=tuple(
                replace(item, provenance=replace(item.provenance, revision="c" * 40))
                if item.collector != "estate"
                else item
                for item in snapshot.collection.observations
            ),
            coverage=tuple(
                replace(item, revision="c" * 40)
                for item in snapshot.collection.coverage
            ),
            inventories=tuple(
                replace(item, revision="c" * 40)
                for item in snapshot.collection.inventories
            ),
        ),
    )
    updated, _ = reconcile(updated.collection)
    assert (
        render_documents(updated).documents[0].sources
        != render_documents(snapshot).documents[0].sources
    )
    second = publish(updated, service.client(), state, "approved-parent")
    assert second.complete
    assert all(item.state == "unchanged" for item in second.artifacts)
    assert tuple(item.file_id for item in second.artifacts) == files
    assert len(service.writes) == 15
    with database_connection(state, "architecture_publication") as connection:
        row = connection.execute(
            "SELECT committed FROM identities WHERE key=%s",
            (DocumentKind.OVERVIEW,),
        ).fetchone()
        assert row is not None
        committed = json.loads(row[0])
        assert committed["snapshot_id"] == snapshot.id
        assert committed["sources"]
        assert connection.execute("SELECT count(*) FROM events").fetchone() == (30,)


def test_material_update_in_place_and_partial_evidence_protection(
    database_url: str,
) -> None:
    service = DriveService()
    drive = service.client()
    state = database_url
    snapshot = representative()
    initial = publish(snapshot, drive, state, "parent")
    before = service.texts.copy()
    blocked = publish(
        partial(snapshot),
        drive,
        state,
        "parent",
    )
    assert not blocked.complete
    assert all(item.state == "blocked" for item in blocked.artifacts)
    assert before == service.texts
    assert len(service.writes) == 15
    changed = publish(
        snapshot, drive, state, "parent", config=RenderConfig("Team 😀 / ")
    )
    assert changed.complete
    assert [item.file_id for item in initial.artifacts] == [
        item.file_id for item in changed.artifacts
    ]
    assert len(service.files) == 17
    assert len(service.writes) == 30
    assert all(
        text.startswith("# Team 😀")
        for key, text in service.texts.items()
        if service.files[key]["mimeType"] == DOCUMENT
    )
    assert publish(
        snapshot, drive, state, "parent", config=RenderConfig("Team 😀 / ")
    ).complete
    assert len(service.writes) == 30


def test_blocked_first_run_performs_no_drive_requests(database_url: str) -> None:
    service = DriveService()
    snapshot = representative()
    result = publish(
        partial(snapshot),
        service.client(),
        database_url,
        "parent",
    )
    assert not result.complete
    assert not service.requests


def test_incomplete_estate_preserves_committed_state_with_zero_remote_calls(
    database_url: str,
) -> None:
    service = DriveService()
    snapshot = representative()
    drive = service.client()
    assert publish(snapshot, drive, database_url, "parent").complete
    with database_connection(database_url, "architecture_publication") as connection:
        before = connection.execute(
            "SELECT key, file_id, committed, pending, attempted "
            "FROM identities ORDER BY key"
        ).fetchall()
    requests = len(service.requests)
    texts = service.texts.copy()
    contract_fact = next(
        fact
        for fact in snapshot.collection.observations
        if fact.key == "estate.contract"
    )
    contract = contract_from_data(json.loads(contract_fact.value))
    expanded = replace(
        contract,
        repositories=(
            *contract.repositories,
            ExpectedRepository("fixture/missing", "core", True),
        ),
    )
    candidate, _ = reconcile(
        replace(
            snapshot.collection,
            observations=tuple(
                expanded.observation() if fact.key == "estate.contract" else fact
                for fact in snapshot.collection.observations
            ),
        ),
        snapshot,
    )
    assert candidate.collection.complete
    assert candidate.estate_state == "incomplete_estate"
    result = publish(candidate, drive, database_url, "parent")
    assert not result.complete
    assert all(item.reason == "incomplete_estate" for item in result.artifacts)
    assert len(service.requests) == requests
    assert service.texts == texts
    with database_connection(database_url, "architecture_publication") as connection:
        after = connection.execute(
            "SELECT key, file_id, committed, pending, attempted "
            "FROM identities ORDER BY key"
        ).fetchall()
    assert after == before


@pytest.mark.parametrize("failure", ["after", "revision"])
def test_ambiguous_update_recovery_and_revision_rejection(
    database_url: str, failure: str
) -> None:
    service = DriveService()
    service.update_failure = failure
    state = database_url
    snapshot = representative()
    first = publish(snapshot, service.client(), state, "parent")
    assert first.artifacts[0].state == "failed"
    assert "fixture-sensitive-literal" not in first.to_json()
    count = len(service.writes)
    second = publish(snapshot, service.client(), state, "parent")
    assert second.complete
    assert second.artifacts[0].state == (
        "recovered" if failure == "after" else "published"
    )
    assert len(service.writes) == count + (failure == "revision")
    assert len(service.files) == 17


@pytest.mark.parametrize("failure", ["before", "after"])
def test_uncertain_creation_never_duplicates(database_url: str, failure: str) -> None:
    service = DriveService()
    service.create_failure = failure
    snapshot = representative()
    state = database_url
    first = publish(snapshot, service.client(), state, "parent")
    assert not first.complete
    second = publish(snapshot, service.client(), state, "parent")
    if failure == "after":
        assert second.complete
        assert len(service.files) == 17
    else:
        assert not second.complete
        assert not service.files
        assert all(item.reason == "creation_unconfirmed" for item in second.artifacts)
    assert "fixture-sensitive-literal" not in first.to_json()


def test_duplicate_suppression_missing_identity_and_scope_guard(
    database_url: str,
) -> None:
    service = DriveService()
    drive = service.client()
    snapshot = representative()
    state = database_url
    first = publish(snapshot, drive, state, "parent")
    file_id = first.artifacts[0].file_id
    assert file_id is not None
    service.files["duplicate"] = {**service.files[file_id], "id": "duplicate"}
    result = publish(snapshot, drive, state, "parent")
    assert result.artifacts[0].reason == "ambiguous_identity"
    del service.files["duplicate"]
    service.files[file_id]["trashed"] = True
    result = publish(snapshot, drive, state, "parent")
    assert result.artifacts[0].reason == "identity_missing_or_changed"
    assert len(service.files) == 17
    assert len(service.writes) == 15
    with pytest.raises(PublicationError, match="scope_mismatch"):
        publish(snapshot, drive, state, "other-parent")


def test_content_conflict_and_readback_protection(database_url: str) -> None:
    service = DriveService()
    service.update_failure = "corrupt"
    state = database_url
    snapshot = representative()
    first = publish(snapshot, service.client(), state, "parent")
    assert first.artifacts[0].reason == "publication_readback_mismatch"
    before = service.texts.copy()
    second = publish(snapshot, service.client(), state, "parent")
    assert second.artifacts[0].reason == "document_content_conflict"
    assert before == service.texts


def test_recovery_adopts_remote_ids_but_refuses_unowned_content(
    database_url: str, database_factory: Callable[[], str]
) -> None:
    service = DriveService()
    snapshot = representative()
    publish(snapshot, service.client(), database_url, "parent")
    result = publish(snapshot, service.client(), database_factory(), "parent")
    assert all(item.reason == "document_content_conflict" for item in result.artifacts)
    assert len(service.files) == 17


def test_render_failure_prevents_all_remote_work(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot = representative()
    rendered = render_documents(snapshot)
    service = DriveService()
    monkeypatch.setattr(
        "architecture_docs.publishing.render_documents",
        lambda *_args: replace(rendered, documents=rendered.documents[:-1]),
    )
    with pytest.raises(PublicationError, match="invalid_rendered_artifacts"):
        publish(snapshot, service.client(), database_url, "parent")
    assert not service.requests


def test_only_materially_affected_documents_are_updated(database_url: str) -> None:
    source = observation("architecture.node", node("api", host="linux"))
    first, _ = reconcile(complete(source))
    second, _ = reconcile(
        complete(replace(source, value=node("api", host="other-linux"))), first
    )
    before = render_documents(first)
    after = render_documents(second)
    affected = {
        left.id
        for left, right in zip(before.documents, after.documents, strict=True)
        if left.content_hash != right.content_hash
    }
    assert affected
    assert len(affected) < len(DocumentKind)
    service = DriveService()
    state = database_url
    assert publish(first, service.client(), state, "parent").complete
    service.writes.clear()
    result = publish(second, service.client(), state, "parent")
    assert result.complete
    assert {
        item.artifact for item in result.artifacts if item.state == "published"
    } == affected
    assert len(service.writes) == len(affected)


def test_presentation_version_replaces_existing_documents_and_reruns_are_idle(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot = representative()
    current = render_documents(snapshot)
    old = replace(
        current,
        documents=tuple(replace(d, renderer_version=1) for d in current.documents),
    )
    service = DriveService()
    monkeypatch.setattr("architecture_docs.publishing.render_documents", lambda *_: old)
    assert publish(snapshot, service.client(), database_url, "parent").complete
    identities = set(service.files)
    service.writes.clear()
    monkeypatch.setattr(
        "architecture_docs.publishing.render_documents", render_documents
    )
    result = publish(snapshot, service.client(), database_url, "parent")
    assert result.complete
    assert all(a.state == "published" for a in result.artifacts)
    assert len(service.writes) == 15
    assert set(service.files) == identities
    service.writes.clear()
    assert publish(snapshot, service.client(), database_url, "parent").complete
    assert not service.writes


def test_definitive_create_rejection_can_retry_and_metadata_mismatch_blocks(
    database_url: str,
) -> None:
    service = DriveService()
    reject = True

    def transport(request: httpx.Request) -> httpx.Response:
        if reject and request.method == "POST":
            return httpx.Response(401, json={"error": "fixture-sensitive-literal"})
        return service.handle(request)

    drive = GoogleDrive("fixture-token", transport=httpx.MockTransport(transport))
    snapshot = representative()
    state = database_url
    failed = publish(snapshot, drive, state, "parent")
    assert all(item.reason == "unauthorized" for item in failed.artifacts)
    reject = False
    result = publish(snapshot, drive, state, "parent")
    assert result.complete
    file_id = result.artifacts[0].file_id
    assert file_id is not None
    service.files[file_id]["mimeType"] = "text/plain"
    before = service.texts.copy()
    blocked = publish(snapshot, drive, state, "parent")
    assert blocked.artifacts[0].reason == "identity_metadata_mismatch"
    assert before == service.texts


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (
            httpx.Response(401, json={"error": "fixture-sensitive-literal"}),
            "unauthorized",
        ),
        (
            httpx.Response(302, headers={"Location": "https://evil.invalid"}),
            "http_error",
        ),
        (
            httpx.Response(200, content=b"invalid-sensitive-literal"),
            "transport_or_decode_error",
        ),
        (httpx.Response(200, json=[]), "malformed_response"),
        (httpx.Response(200, content=b"x" * 8_388_609), "response_too_large"),
    ],
)
def test_transport_errors_are_bounded_and_sanitized(
    response: httpx.Response, reason: str
) -> None:
    drive = GoogleDrive(
        "fixture-token", transport=httpx.MockTransport(lambda _request: response)
    )
    with pytest.raises(PublicationError, match=reason):
        drive.file("file")
    drive.close()


def test_store_and_transport_invalid_contracts(database_url: str) -> None:
    for bad in ("", "x\n", "a' or true", "../file"):
        with pytest.raises(PublicationError):
            identity(bad)
    for token in ("", "bad\n"):
        with pytest.raises(PublicationError, match="token"):
            GoogleDrive(token)
    for url in ("", ":memory:", "relative.sqlite3"):
        with pytest.raises(PublicationError, match="database_url"):
            PublicationStore(url)
    with (
        pytest.raises(PublicationError, match="unavailable"),
        PublicationStore("postgresql://fixture:fixture@127.0.0.1:1/missing").locked(
            "scope"
        ),
    ):
        pytest.fail("unavailable database must fail")
    state = database_url
    with PublicationStore(state).locked("scope"):
        pass
    with database_connection(state, "architecture_publication") as connection:
        connection.execute("UPDATE schema_version SET version=2")
    with (
        pytest.raises(PublicationError, match="unsupported_publication_schema"),
        PublicationStore(state).locked("scope"),
    ):
        pytest.fail("future schema must fail")
    with database_connection(state, "architecture_publication") as connection:
        connection.execute("UPDATE schema_version SET version=1")
    with PublicationStore(state).locked("scope") as connection:
        connection.commit()
        with (
            pytest.raises(PublicationError, match="busy"),
            PublicationStore(state).locked("scope"),
        ):
            pytest.fail("second writer must fail even after commit")
        with pytest.raises(PublicationError, match="invalid_state_field"):
            save_state(connection, "key", "bad", {})
    service = DriveService()
    service.list_failure = True
    with pytest.raises(PublicationError, match="scope_mismatch"):
        publish(representative(), service.client(), state, "parent")
    drive = GoogleDrive(
        "token",
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json={})),
    )
    with pytest.raises(PublicationError, match="malformed_response"):
        drive.find("parent", "scope", "key")


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"revisionId": "1", "body": {"content": [{"table": {}}]}},
        {"revisionId": "1", "body": {"content": []}, "tabs": [{}]},
        {
            "revisionId": "1",
            "body": {"content": [{"paragraph": {"bullet": {"listId": "list"}}}]},
        },
        {
            "revisionId": "1",
            "body": {
                "content": [{"paragraph": {"elements": [{"inlineObjectElement": {}}]}}]
            },
        },
        {
            "revisionId": "1",
            "body": {
                "content": [{"paragraph": {"elements": [{"textRun": {"content": 1}}]}}]
            },
        },
        {
            "revisionId": "1",
            "body": {
                "content": [
                    {"paragraph": {"elements": [{"suggestedInsertionIds": ["x"]}]}}
                ]
            },
        },
        {
            "revisionId": "1",
            "body": {
                "content": [
                    {
                        "endIndex": "2",
                        "paragraph": {"elements": [{"textRun": {"content": "\n"}}]},
                    }
                ]
            },
        },
        {
            "revisionId": "1",
            "body": {
                "content": [
                    {
                        "endIndex": 3,
                        "paragraph": {"elements": [{"textRun": {"content": "\n"}}]},
                    }
                ]
            },
        },
    ],
)
def test_unsupported_docs_structure_is_protected(body: dict[str, Any]) -> None:
    with pytest.raises(PublicationError):
        document_text(body)


def test_cli_reports_summary_without_credentials(
    tmp_path: Path,
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    service = DriveService()
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(representative().to_json())
    args = [str(snapshot), "--parent", "parent"]
    monkeypatch.setenv("ARCHITECTURE_DOCS_GOOGLE_ACCESS_TOKEN", "fixture-token")
    monkeypatch.setenv("ARCHITECTURE_DOCS_DATABASE_URL", database_url)
    monkeypatch.setattr(
        "architecture_docs.publishing.__main__.GoogleDrive",
        lambda _token: service.client(),
    )
    assert main(args) == 0
    output = capsys.readouterr().out
    assert json.loads(output)["complete"] is True
    assert "fixture-token" not in output
    blocked = representative()
    snapshot.write_text(partial(blocked).to_json())
    assert main(args) == 1
    capsys.readouterr()
    snapshot.write_text("fixture-sensitive-literal")
    with pytest.raises(SystemExit) as error:
        main(args)
    assert error.value.code == 2
    assert "fixture-sensitive-literal" not in capsys.readouterr().err
