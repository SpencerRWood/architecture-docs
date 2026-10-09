"""Withdrawn collection scope removes owned evidence, retaining external references."""

from dataclasses import replace
from pathlib import Path

import httpx

from architecture_docs.codec import snapshot_from_json
from architecture_docs.collection import collect
from architecture_docs.collectors.github import GitHub
from architecture_docs.config import load_registry
from architecture_docs.declarations import NodeKind
from architecture_docs.model import Authority, SourceCoverage
from architecture_docs.reconciliation import load_policy, reconcile
from architecture_docs.renderers import render_documents
from architecture_docs.renderers.artifacts import DocumentKind
from test_estate import full_estate
from test_reconciliation import observation

HOMELAB = "SpencerRWood/homelab"


def test_approved_collection_never_requests_excluded_repositories() -> None:
    registry = load_registry(Path("config/repositories.toml"))
    paths = []

    def respond(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(404)

    github = GitHub("fixture-token", transport=httpx.MockTransport(respond))
    try:
        collection = collect(registry, github)
    finally:
        github.close()
    assert paths == [f"/repos/{repo.name}" for repo in registry.repositories]
    assert len(paths) == 29
    for excluded in (
        HOMELAB,
        "SpencerRWood/wood-data-platform",
        "SpencerRWood/sql-control-cli",
        "SpencerRWood/wood-agents",
        "SpencerRWood/woodanalytics-site",
        "SpencerRWood/website-marketing-simulation",
    ):
        assert all(excluded not in path for path in paths)
        assert excluded not in collection.observations[0].value


def test_explicit_scope_removal_clears_historical_evidence_and_keeps_references() -> (
    None
):
    current = full_estate()
    reference = observation(
        "documentation.repository_reference",
        HOMELAB,
        "README.md",
        Authority.DOCUMENTATION,
        repository="fixture/service",
    )
    current = replace(
        current,
        observations=(*current.observations, reference),
        coverage=(
            *current.coverage,
            SourceCoverage("fixture/service", "README.md", "fixture", "a" * 40),
        ),
    )
    historical = tuple(
        observation(key, value, source, repository=HOMELAB)
        for key, value, source in (
            ("compose.services", "caddy", "compose/platform/caddy/compose.yml"),
            (
                "compose.services",
                "code-server",
                "compose/platform/code-server/compose.yml",
            ),
            ("compose.services", "audiobookshelf", "compose/books/compose.yml"),
            ("compose.volumes", "media-storage", "compose/books/compose.yml"),
            (
                "workflow.secret_name",
                "HOMELAB_ONLY_TOKEN",
                ".github/workflows/deploy.yml",
            ),
            ("workflow.job", "deploy", ".github/workflows/deploy.yml"),
            ("workflow.job", "rollback", ".github/workflows/rollback.yml"),
            ("contract.kind", "shell", "scripts/deploy-infisical-service"),
            ("contract.kind", "shell", "scripts/rollback-infisical-service"),
            ("contract.kind", "shell", "scripts/health-check.sh"),
        )
    )
    previous, _ = reconcile(
        replace(
            current,
            observations=(*current.observations, *historical),
            coverage=tuple(
                sorted(
                    set(current.coverage)
                    | {
                        SourceCoverage(
                            HOMELAB, fact.provenance.source, "fixture", "a" * 40
                        )
                        for fact in historical
                    }
                )
            ),
        )
    )
    assert any(
        node.repository == HOMELAB and node.declared for node in previous.graph.nodes
    )
    policy = load_policy(Path("config/reconciliation.toml"))
    snapshot, diff = reconcile(current, previous, policy)
    assert snapshot.estate_coverage.complete
    assert len(snapshot.estate_coverage.domains) == 6
    assert not snapshot.publication_blocked
    assert all(item.name != HOMELAB for item in snapshot.estate_coverage.repositories)
    assert all(
        item.observation.provenance.repository != HOMELAB for item in snapshot.evidence
    )
    references = [node for node in snapshot.graph.nodes if node.repository == HOMELAB]
    assert len(references) == 1
    assert references[0].kind == NodeKind.REPOSITORY
    assert not references[0].declared
    assert references[0].evidence[0].observation == reference
    assert any(change.operation == "removed" for change in diff.changes)
    assert snapshot_from_json(snapshot.to_json()) == snapshot
    corpus = render_documents(snapshot)
    for document in corpus.documents:
        assert all(source.repository != HOMELAB for source in document.sources)
        for owned in (
            "HOMELAB_ONLY_TOKEN",
            "code-server",
            "audiobookshelf",
            "media-storage",
            "deploy-infisical-service",
            "rollback-infisical-service",
            "health-check.sh",
        ):
            assert owned not in document.to_markdown()
    catalog = next(doc for doc in corpus.documents if doc.id == DocumentKind.CATALOG)
    external = next(
        section
        for section in catalog.sections
        if section.id == "referenced-repositories"
    )
    assert any(
        HOMELAB in row.label and not row.state.startswith("declared")
        for row in external.rows
    )
    assert (
        render_documents(reconcile(current, policy=policy)[0]).to_json()
        == corpus.to_json()
    )


def test_unreferenced_withdrawn_repository_leaves_no_current_graph_entities() -> None:
    collection = full_estate()
    previous, _ = reconcile(
        replace(
            collection,
            observations=(
                *collection.observations,
                observation(
                    "compose.services", "caddy", "compose.yml", repository=HOMELAB
                ),
            ),
        )
    )
    snapshot, _ = reconcile(
        collection, previous, load_policy(Path("config/reconciliation.toml"))
    )
    assert not any(node.repository == HOMELAB for node in snapshot.graph.nodes)
    assert snapshot.estate_coverage.complete
    assert HOMELAB not in render_documents(snapshot).to_json()
