"""Manual collection and durable reconciliation; nightly scheduling comes later."""

import os
from pathlib import Path

from dagster import AssetExecutionContext, Config, asset

from architecture_docs.codec import collection_from_json, snapshot_from_json
from architecture_docs.collection import collect
from architecture_docs.collectors.github import GitHub
from architecture_docs.config import load_registry
from architecture_docs.reconciliation import DEFAULT_POLICY, load_policy
from architecture_docs.renderers import render_documents
from architecture_docs.store import SnapshotStore


class CollectionConfig(Config):
    registry_path: str


@asset
def repository_observations(
    context: AssetExecutionContext, config: CollectionConfig
) -> str:
    github = GitHub(os.environ.get("ARCHITECTURE_DOCS_GITHUB_TOKEN"))
    try:
        result = collect(load_registry(Path(config.registry_path)), github)
    finally:
        github.close()
    context.add_output_metadata(
        {
            "complete": result.complete,
            "observation_count": len(result.observations),
            "failure_count": len(result.failures),
            "schema_version": result.schema_version,
        }
    )
    return result.to_json()


class ReconciliationConfig(Config):
    policy_path: str | None = None


@asset
def architecture_snapshot(
    context: AssetExecutionContext,
    config: ReconciliationConfig,
    repository_observations: str,
) -> str:
    policy = (
        load_policy(Path(config.policy_path)) if config.policy_path else DEFAULT_POLICY
    )
    record = SnapshotStore().reconcile(
        collection_from_json(repository_observations),
        policy,
    )
    context.add_output_metadata(
        {
            "snapshot_id": record.snapshot.id,
            "previous_snapshot_id": record.diff.previous or "none",
            "ledger_run_id": record.run_id,
            "material": record.diff.material,
            "change_count": len(record.diff.changes),
            "publication_blocked": record.snapshot.publication_blocked,
            "failure_count": len(record.snapshot.collection.failures),
            "schema_version": record.snapshot.schema_version,
        }
    )
    return record.snapshot.to_json()


@asset
def architecture_documents(
    context: AssetExecutionContext, architecture_snapshot: str
) -> str:
    documents = render_documents(snapshot_from_json(architecture_snapshot))
    context.add_output_metadata(
        {
            "snapshot_id": documents.snapshot_id,
            "document_count": len(documents.documents),
            "publication_blocked": any(
                doc.publication_blocked for doc in documents.documents
            ),
            "schema_version": documents.schema_version,
        }
    )
    return documents.to_json()
