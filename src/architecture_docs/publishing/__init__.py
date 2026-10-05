"""Restart-safe publication of deterministic snapshots into stable native Docs."""

import sqlite3
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from architecture_docs.declarations import canonical
from architecture_docs.publishing.google_drive import (
    DOCUMENT,
    FOLDER,
    GoogleDrive,
    PublicationError,
    document_text,
    identity,
)
from architecture_docs.publishing.store import PublicationStore, record, save_state
from architecture_docs.reconciliation import Snapshot
from architecture_docs.renderers import render_documents
from architecture_docs.renderers.artifacts import (
    DEFAULT_CONFIG,
    Document,
    DocumentKind,
    RenderConfig,
    digest,
)


@dataclass(frozen=True)
class ArtifactResult:
    artifact: str
    state: str
    file_id: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class PublicationResult:
    snapshot_id: str
    artifacts: tuple[ArtifactResult, ...]

    @property
    def complete(self) -> bool:
        return all(
            item.state in {"published", "unchanged", "recovered"}
            for item in self.artifacts
        )

    def to_json(self) -> str:
        return canonical({**asdict(self), "complete": self.complete})


def text_hash(text: str) -> str:
    return sha256(text.encode()).hexdigest()


def ensure_file(  # noqa: PLR0913, PLR0917 -- explicit remote identity selectors
    connection: sqlite3.Connection,
    drive: GoogleDrive,
    scope: str,
    parent: str,
    key: str,
    title: str,
    mime: str,
) -> str:
    saved = record(connection, key)
    found = drive.find(parent, scope, key)
    if saved["file_id"]:
        file = drive.file(saved["file_id"])
        if found is None or found.get("id") != saved["file_id"]:
            raise PublicationError("identity_missing_or_changed")
    elif found:
        file = found
    else:
        if saved["attempted"]:
            raise PublicationError("creation_unconfirmed")
        # Commit before sending: ambiguous creates must never be blindly retried.
        connection.execute("UPDATE identities SET attempted=1 WHERE key=?", (key,))
        connection.commit()
        try:
            file = drive.create(parent, scope, key, title, mime)
        except PublicationError as error:
            if str(error) in {
                "invalid_request_or_revision",
                "unauthorized",
                "access_denied",
                "missing_file",
                "rate_limit",
            }:
                # A definitive API rejection did not create the file.
                connection.execute(
                    "UPDATE identities SET attempted=0 WHERE key=?", (key,)
                )
                connection.commit()
            raise
    file_id = identity(file.get("id"))
    if (
        file.get("mimeType") != mime
        or file.get("trashed") is not False
        or file.get("parents") != [parent]
        or file.get("appProperties")
        != {
            "architecture_scope": scope,
            "architecture_artifact": key,
        }
    ):
        raise PublicationError("identity_metadata_mismatch")
    connection.execute("UPDATE identities SET file_id=? WHERE key=?", (file_id, key))
    connection.commit()
    return file_id


def metadata(document: Document, text: str) -> dict[str, object]:
    return {
        "snapshot_id": document.snapshot_id,
        "content_hash": document.content_hash,
        "configuration_id": document.configuration_id,
        "renderer_version": document.renderer_version,
        "schema_version": document.schema_version,
        "text_hash": text_hash(text),
        "sources": [asdict(source) for source in document.sources],
    }


def publish_document(
    connection: sqlite3.Connection,
    drive: GoogleDrive,
    file_id: str,
    document: Document,
    text: str,
) -> ArtifactResult:
    saved = record(connection, document.id)
    current, revision, end = document_text(drive.document(file_id))
    current_hash = text_hash(current)
    committed: dict[str, Any] | None = saved["committed"]
    pending: dict[str, Any] | None = saved["pending"]
    recovered = False
    if pending and current_hash == pending["text_hash"]:
        save_state(connection, document.id, "committed", pending)
        committed = pending
        recovered = True
    elif current_hash != (committed["text_hash"] if committed else text_hash("")):
        raise PublicationError("document_content_conflict")
    # Unaffected documents keep their actual published snapshot/provenance.
    if committed and committed["content_hash"] == document.content_hash:
        return ArtifactResult(
            document.id, "recovered" if recovered else "unchanged", file_id
        )
    target = metadata(document, text)
    save_state(connection, document.id, "pending", target)
    drive.replace(file_id, revision, end, text)
    actual, _, _ = document_text(drive.document(file_id))
    if text_hash(actual) != target["text_hash"]:
        raise PublicationError("publication_readback_mismatch")
    save_state(connection, document.id, "committed", target)
    return ArtifactResult(document.id, "published", file_id)


def publish(  # noqa: PLR0913 -- explicit storage, destination and render config
    snapshot: Snapshot,
    drive: GoogleDrive,
    state_path: Path,
    parent_id: str,
    *,
    namespace: str = "architecture-docs",
    config: RenderConfig = DEFAULT_CONFIG,
) -> PublicationResult:
    """Render/validate the whole set before touching Drive; isolate remote failures."""
    identity(parent_id)
    identity(namespace)
    documents = render_documents(snapshot, config)
    if (
        documents.schema_version != 1
        or {doc.id for doc in documents.documents} != set(DocumentKind)
        or len(documents.documents) != len(DocumentKind)
        or any(
            doc.snapshot_id != snapshot.id
            or doc.schema_version != 1
            or doc.generation_state not in {"complete", "with_gaps", "blocked"}
            for doc in documents.documents
        )
    ):
        raise PublicationError("invalid_rendered_artifacts")
    outputs = {doc.id: doc.to_markdown(config) for doc in documents.documents}
    scope = digest((parent_id, namespace))
    results = []
    with PublicationStore(state_path).locked(scope) as connection:
        folders: dict[str, str] = {}
        for doc in documents.documents:
            file_id = record(connection, doc.id)["file_id"]
            try:
                if snapshot.publication_blocked or doc.publication_blocked:
                    result = ArtifactResult(
                        doc.id, "blocked", file_id, "incomplete_evidence"
                    )
                else:
                    if not folders:
                        folders["architecture"] = ensure_file(
                            connection,
                            drive,
                            scope,
                            parent_id,
                            "architecture",
                            "Architecture",
                            FOLDER,
                        )
                    parent = folders["architecture"]
                    if doc.id.startswith("runbook-"):
                        if "runbooks" not in folders:
                            folders["runbooks"] = ensure_file(
                                connection,
                                drive,
                                scope,
                                parent,
                                "runbooks",
                                "Runbooks",
                                FOLDER,
                            )
                        parent = folders["runbooks"]
                    file_id = ensure_file(
                        connection, drive, scope, parent, doc.id, doc.title, DOCUMENT
                    )
                    result = publish_document(
                        connection, drive, file_id, doc, outputs[doc.id]
                    )
            except PublicationError as error:
                result = ArtifactResult(doc.id, "failed", file_id, str(error))
            connection.execute(
                "INSERT INTO events(snapshot_id,artifact,state,reason,file_id) "
                "VALUES (?,?,?,?,?)",
                (snapshot.id, doc.id, result.state, result.reason, result.file_id),
            )
            connection.commit()
            results.append(result)
    return PublicationResult(snapshot.id, tuple(results))
