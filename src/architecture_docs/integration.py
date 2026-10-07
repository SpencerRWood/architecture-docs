"""Regenerate a local candidate with live approved Infisical metadata.

Retained GitHub evidence stays pinned. Optional local architecture.toml has
honest worktree provenance. Publication requires an explicit approved parent.
"""

import argparse
import json
import os
import sys
from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from architecture_docs.codec import snapshot_from_json
from architecture_docs.collectors.content import configuration
from architecture_docs.collectors.contracts import SourceFile
from architecture_docs.collectors.github import GitHub
from architecture_docs.collectors.infisical import Infisical
from architecture_docs.config import load_registry
from architecture_docs.database import database_url
from architecture_docs.declarations import canonical
from architecture_docs.model import Authority, Observation, Provenance, SourceCoverage
from architecture_docs.publishing import PublicationResult, publish
from architecture_docs.publishing.google_drive import GoogleDrive, PublicationError
from architecture_docs.reconciliation import Snapshot, load_policy, reconcile
from architecture_docs.renderers import render_documents
from architecture_docs.secret_mappings import mappings
from architecture_docs.workflow_refresh import refresh_workflows

REPOSITORY = "SpencerRWood/architecture-docs"


def publish_candidate(snapshot: Snapshot, parent: str) -> PublicationResult:
    drive = GoogleDrive(os.environ.get("ARCHITECTURE_DOCS_GOOGLE_ACCESS_TOKEN", ""))
    try:
        return publish(snapshot, drive, database_url(), parent)
    finally:
        drive.close()


def main(arguments: list[str] | None = None) -> int:  # noqa: PLR0915 - bounded integration phases
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("previous_snapshot", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--registry", type=Path, default=Path("config/repositories.toml")
    )
    parser.add_argument(
        "--policy", type=Path, default=Path("config/reconciliation.toml")
    )
    parser.add_argument("--worktree-contract", type=Path)
    parser.add_argument(
        "--refresh-workflows",
        action="store_true",
        help="Re-extract approved pinned workflow blobs for caller/secret metadata",
    )
    parser.add_argument(
        "--publish-parent",
        help="Publish through the durable ledger to the approved Drive parent",
    )
    args = parser.parse_args(arguments)
    try:
        registry = load_registry(args.registry)
        if registry.infisical is None or registry.estate is None:
            raise ValueError("integration requires explicit approvals")
        previous = snapshot_from_json(args.previous_snapshot.read_text())
        approved = {r.name for r in registry.repositories}
        source = Infisical.from_environment(registry.infisical)
        try:
            live = source.collect()
        finally:
            source.close()
        retained = previous.collection
        if args.refresh_workflows:
            github = GitHub(os.environ.get("ARCHITECTURE_DOCS_GITHUB_TOKEN"))
            try:
                retained = refresh_workflows(retained, registry, github)
            finally:
                github.close()
        collection = replace(
            retained,
            observations=(
                *(
                    o
                    for o in retained.observations
                    if o.provenance.repository in approved
                    and o.key != "estate.contract"
                    and not o.key.startswith("infisical.")
                ),
                registry.estate.observation(),
                *live.observations,
            ),
            failures=tuple(
                f
                for f in retained.failures
                if f.provenance.repository in approved
                and not f.provenance.source.startswith("infisical:")
            )
            + live.failures,
            coverage=tuple(
                c
                for c in retained.coverage
                if c.repository in approved and not c.source.startswith("infisical:")
            )
            + live.coverage,
            inventories=tuple(
                i for i in retained.inventories if i.repository in approved
            ),
            skipped=tuple(r for r in retained.skipped if r in approved),
        )
        if args.worktree_contract is not None:
            repo = next(r for r in registry.repositories if r.name == REPOSITORY)
            if args.worktree_contract != Path("architecture.toml") or not repo.approves(
                "architecture.toml"
            ):
                raise ValueError("unapproved worktree contract")
            content = args.worktree_contract.read_text()
            provenance = Provenance(
                REPOSITORY,
                "worktree:architecture.toml",
                None,
                sha256(content.encode()).hexdigest(),
            )
            facts = configuration(
                SourceFile(Provenance(REPOSITORY, "architecture.toml", None), content)
            )
            collection = replace(
                collection,
                observations=tuple(
                    o
                    for o in collection.observations
                    if not (
                        o.provenance.repository == REPOSITORY
                        and o.provenance.source
                        in {"architecture.toml", "worktree:architecture.toml"}
                    )
                )
                + tuple(
                    Observation(
                        "integration-worktree",
                        Authority.CONFIGURATION,
                        k,
                        v,
                        provenance,
                    )
                    for k, v in facts
                ),
                coverage=(
                    *(
                        c
                        for c in collection.coverage
                        if not (
                            c.repository == REPOSITORY
                            and c.source
                            in {"architecture.toml", "worktree:architecture.toml"}
                        )
                    ),
                    SourceCoverage(
                        REPOSITORY, provenance.source, "integration-worktree", None
                    ),
                ),
            )
        snapshot, diff = reconcile(collection, previous, load_policy(args.policy))
        documents = render_documents(snapshot)
        previous_documents = {d.id: d for d in render_documents(previous).documents}
        entries, unused = mappings(snapshot)
        report = {
            "observed_at": datetime.now(UTC).isoformat(),
            "mode": "reextracted-workflows/live-Infisical/local-contract-candidate"
            if args.refresh_workflows
            else "retained-pinned-GitHub/live-Infisical/local-contract-candidate",
            "snapshot_id": snapshot.id,
            "snapshot_file": str(args.output / "snapshot.json"),
            "previous_snapshot_id": previous.id,
            "document_count": len(documents.documents),
            "metadata_collection_complete": live.complete,
            "failures": [f.reason for f in live.failures],
            "mapped": sum(m.status == "mapped" for m in entries),
            "stale": sum(m.status == "stale" for m in entries),
            "unresolved": sum(m.status == "unresolved" for m in entries),
            "known_github_references": sum(m.status == "resolved" for m in entries),
            "unbound_optional_workflow_parameters": sum(
                m.status == "not-forwarded" for m in entries
            ),
            "ambiguous": sum(m.status == "ambiguous" for m in entries),
            "unconsumed_metadata": len(unused),
            "publication_blocked": snapshot.publication_blocked,
            "material_changes": len(diff.changes),
            "material_categories": sorted({c.category for c in diff.changes}),
            "affected_documents": [
                d.id
                for d in documents.documents
                if d.content_hash != previous_documents[d.id].content_hash
            ],
            "publication_requested": args.publish_parent is not None,
            "publication": None,
            "live_mapping_verified": live.complete
            and any(m.status == "mapped" for m in entries),
            "limitation": (
                "Workflows re-extracted from pinned GitHub blobs; "
                "other GitHub evidence retained. "
                if args.refresh_workflows
                else "GitHub evidence is retained, not freshly collected. "
            )
            + "Local contract has no Git revision. Local regeneration alone does "
            "not update Drive. Publication compares against the durable published "
            "ledger, independently of the previous local snapshot.",
        }
        outputs = {f"{d.id}.md": d.to_markdown() for d in documents.documents}
        outputs.update(
            {
                "snapshot.json": snapshot.to_json(),
                "document-set.json": documents.to_json(),
                "diff.json": diff.to_json(),
                "review.json": canonical(report),
            }
        )
        args.output.mkdir(parents=True, exist_ok=True)
        for filename, content in outputs.items():
            (args.output / filename).write_text(
                content if filename == "snapshot.json" else content + "\n"
            )
        ready = report["live_mapping_verified"] and not snapshot.publication_blocked
        if args.publish_parent is not None and ready:
            publication = publish_candidate(snapshot, args.publish_parent)
            report["publication"] = json.loads(publication.to_json())
            ready = publication.complete
        (args.output / "review.json").write_text(canonical(report) + "\n")
        sys.stdout.write(canonical(report) + "\n")
        return 0 if ready else 2
    except PublicationError, ValueError, OSError, StopIteration:
        parser.exit(2, "Unable to generate approved metadata integration candidate.\n")


if __name__ == "__main__":
    raise SystemExit(main())
