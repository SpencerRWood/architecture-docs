"""Offline normalized snapshots prove document content, provenance, and uncertainty."""

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from architecture_docs.collection import collect
from architecture_docs.declarations import EdgeKind, NodeKind, canonical
from architecture_docs.graph import Evidence
from architecture_docs.model import Authority, CollectionResult, Failure
from architecture_docs.reconciliation import Policy, Snapshot, reconcile
from architecture_docs.renderers import render_documents
from architecture_docs.renderers.artifacts import (
    Document,
    DocumentKind,
    RenderConfig,
    Row,
    SourceReference,
    escape,
    source_url,
)
from test_collection import FixtureGitHub, registry
from test_reconciliation import complete, edge, node, observation


def representative() -> Snapshot:
    collection = collect(registry(), FixtureGitHub().client())
    additions = [
        observation(
            "architecture.node",
            node("fixture/service", NodeKind.REPOSITORY, purpose="application"),
        ),
        observation(
            "architecture.node",
            node("fixture/infrastructure", NodeKind.REPOSITORY, purpose="deployment"),
            repository="fixture/infrastructure",
        ),
        observation(
            "architecture.node",
            node("fixture/workflows", NodeKind.REPOSITORY, purpose="shared-delivery"),
            repository="fixture/workflows",
        ),
        observation(
            "architecture.node",
            node(
                "delivery",
                NodeKind.RELEASE_CONTRACT,
                checks="pytest",
                candidate_artifact="ghcr.io/fixture/api",
                runtime_gate="runtime-smoke",
                publish="true",
                promotion="candidate-to-stable",
                deployment="compose.yml",
                rollback="scripts/restore.sh",
                verification="scripts/check.sh",
            ),
        ),
        observation(
            "architecture.node",
            node(
                "http", NodeKind.INTERFACE, network="internal", reverse_proxy="traefik"
            ),
        ),
        observation(
            "architecture.node",
            node(
                "data",
                NodeKind.STORAGE,
                technology="nas",
                backup="scripts/backup.sh",
                recovery="scripts/restore.sh",
            ),
        ),
        observation(
            "architecture.node",
            node(
                "dagster",
                NodeKind.ORCHESTRATION,
                technology="dagster",
                code_location="architecture-docs",
            ),
        ),
    ]
    for capability in (
        "renovate",
        "codex",
        "openproject",
        "reporting",
        "repair",
        "events",
    ):
        additions.append(
            observation(
                "architecture.node",
                node(capability, NodeKind.ORCHESTRATION, technology=capability),
            )
        )
    additions.append(
        observation(
            "architecture.edge",
            canonical(
                {
                    "kind": "consumes_workflow",
                    "source": {"kind": "service", "name": "api"},
                    "target": {
                        "kind": "orchestration",
                        "name": "release",
                        "repository": "fixture/workflows",
                    },
                }
            ),
        )
    )
    additions.extend(
        (
            observation(
                "architecture.node",
                node(
                    "shared",
                    NodeKind.DATABASE,
                    role="operator",
                    schema="reporting",
                    backup="scripts/backup.sh",
                ),
                repository="fixture/infrastructure",
            ),
            observation(
                "architecture.node",
                node("report-worker", NodeKind.SERVICE),
                repository="fixture/workflows",
            ),
            observation(
                "architecture.edge",
                canonical(
                    {
                        "kind": "reads_database",
                        "source": {"kind": "service", "name": "api"},
                        "target": {
                            "kind": "database",
                            "name": "shared",
                            "repository": "fixture/infrastructure",
                        },
                    }
                ),
            ),
            observation(
                "architecture.edge",
                canonical(
                    {
                        "kind": "reads_database",
                        "source": {"kind": "service", "name": "report-worker"},
                        "target": {
                            "kind": "database",
                            "name": "shared",
                            "repository": "fixture/infrastructure",
                        },
                    }
                ),
                repository="fixture/workflows",
            ),
        )
    )
    snapshot, _ = reconcile(
        replace(collection, observations=collection.observations + tuple(additions))
    )
    return snapshot


def document(snapshot: Snapshot, kind: DocumentKind) -> Document:
    return next(
        item for item in render_documents(snapshot).documents if item.id == kind
    )


def rows(doc: Document) -> tuple[Row, ...]:
    return tuple(row for section in doc.sections for row in section.rows)


def test_all_documents_have_stable_ids_generation_metadata_and_sources() -> None:
    snapshot = representative()
    bundle = render_documents(snapshot)
    assert {doc.id for doc in bundle.documents} == set(DocumentKind)
    assert len(bundle.documents) == 15
    assert {
        "Architecture Overview",
        "Repository & Dependency Catalog",
        "Deployment & Release Architecture",
        "Runtime & Infrastructure Architecture",
        "Data & Storage Architecture",
        "Automation & Orchestration Architecture",
    } <= {doc.title for doc in bundle.documents}
    assert bundle.snapshot_id == snapshot.id
    for doc in bundle.documents:
        assert doc.snapshot_id == snapshot.id
        assert doc.schema_version == doc.model_version == doc.graph_version == 1
        assert doc.renderer_version == 2
        assert not doc.publication_blocked
        assert len(doc.content_hash) == 64
        if not doc.id.value.startswith("runbook-"):
            assert doc.sources
        assert len({section.id for section in doc.sections}) == len(doc.sections)
        for section in doc.sections:
            assert len({row.id for row in section.rows}) == len(section.rows)
        assert snapshot.id not in doc.to_markdown()
        assert "Generation state:" in doc.to_markdown()
        assert json.loads(doc.to_json())["id"] == doc.id
        assert json.loads(doc.to_json())["content_hash"] == doc.content_hash
    payload = bundle.to_json()
    for absent in (
        "fixture-sensitive-literal",
        "fixture-token",
        "docker run",
        "postgresql://sensitive",
    ):
        assert absent not in payload
    assert "secret-value" not in payload
    assert "secret_value" not in payload


def test_overview_explains_typed_cross_repository_interactions_with_provenance() -> (
    None
):
    doc = document(representative(), DocumentKind.OVERVIEW)
    text = doc.to_markdown()
    assert "declares deployment ownership in repository fixture/infrastructure" in text
    assert "fixture/workflows / release" not in text  # Reference-only detail.
    assert "declares data access to database fixture/service / analytics" in text
    assert "declares notifications to interface fixture/service / http" in text
    assert "No narrative" not in text
    assert "```mermaid\nflowchart LR" in text
    graph = next(
        section.diagram for section in doc.sections if section.id == "boundaries"
    )
    assert graph is not None
    assert "system fixture/service / platform" in graph
    assert "fixture/infrastructure" in graph
    for row in rows(doc):
        if row.entities and row.state != "gap":
            assert row.sources
            assert all(
                source.repository.startswith("fixture/") for source in row.sources
            )


def test_catalog_entries_distinguish_included_and_referenced_repositories() -> None:
    snapshot = representative()
    doc = document(snapshot, DocumentKind.CATALOG)
    included = {
        node.repository
        for node in snapshot.graph.nodes
        if node.kind == NodeKind.REPOSITORY and node.declared
    }
    entries = [section for section in doc.sections if section.title in included]
    assert len(entries) == len(included) == 3
    assert {entry.title for entry in entries} == included
    app = next(section for section in entries if section.title == "fixture/service")
    assert any(
        row.label.endswith(" / purpose") and row.value == "application"
        for row in app.rows
    )
    assert any(
        row.label == "deployment_owner" and "fixture/infrastructure" in row.value
        for row in app.rows
    )
    assert any(
        row.label == "consumes_workflow" and "fixture/workflows" in row.value
        for row in app.rows
    )
    assert any(section.id == "referenced-repositories" for section in doc.sections)
    assert not any(row.label == "API_TOKEN / project" for row in rows(doc))


def test_deployment_renders_all_evidenced_lifecycle_stages_as_contracts() -> None:
    doc = document(representative(), DocumentKind.DEPLOYMENT)
    expected = {
        "validation": "pytest",
        "candidate": "ghcr.io/fixture/api",
        "runtime-gate": "runtime-smoke",
        "release": "true",
        "promotion": "candidate-to-stable",
        "deployment": "compose.yml",
        "rollback": "scripts/restore.sh",
        "verification": "scripts/check.sh",
    }
    for identity, value in expected.items():
        section = next(section for section in doc.sections if section.id == identity)
        assert any(row.value == value and row.sources for row in section.rows)
    assert "do not attest deployed health" in doc.to_markdown()
    assert "deployment succeeded" not in doc.to_markdown()


def test_runtime_and_data_show_explicit_networking_storage_roles_and_recovery() -> None:
    snapshot = representative()
    runtime = document(snapshot, DocumentKind.RUNTIME).to_markdown()
    data = document(snapshot, DocumentKind.DATA).to_markdown()
    for value in (
        "linux",
        "dev",
        "traefik",
        "internal",
        "architecture-docs",
        "fixture/infrastructure",
    ):
        assert value in runtime
    for value in (
        "analytics",
        "public",
        "application",
        "nas",
        "scripts/backup.sh",
        "scripts/restore.sh",
    ):
        assert value in data
    assert "uses storage storage fixture/service / data" in data
    assert "declares data access to database fixture/service / analytics" in data
    assert (
        "service fixture/service / api declares data access to database "
        "fixture/infrastructure / shared" in data
    )
    assert (
        "service fixture/workflows / report-worker declares data access to database "
        "fixture/infrastructure / shared" in data
    )


def test_generic_workflow_actions_are_not_classified_as_release_publication() -> None:
    snapshot, _ = reconcile(
        complete(
            observation(
                "workflow.uses", "actions/checkout@v4", ".github/workflows/checks.yml"
            )
        )
    )
    doc = document(snapshot, DocumentKind.DEPLOYMENT)
    stage = next(section for section in doc.sections if section.id == "release")
    assert all(row.state == "gap" for row in stage.rows)
    assert all("actions/checkout" not in row.value for row in stage.rows)


def test_automation_shows_only_explicit_capabilities_and_notification_relations() -> (
    None
):
    doc = document(representative(), DocumentKind.AUTOMATION)
    for capability in (
        "dagster",
        "renovate",
        "codex",
        "openproject",
        "reporting",
        "repair",
        "events",
    ):
        section = next(
            section
            for section in doc.sections
            if section.id == f"capability-{capability}"
        )
        assert any(row.value == capability and row.sources for row in section.rows)
        assert not any(row.state == "gap" for row in section.rows)
    assert any(row.label == "notifies" for row in rows(doc))
    missing = document(
        reconcile(
            complete(
                observation(
                    "architecture.node", node("renovate", NodeKind.ORCHESTRATION)
                )
            )
        )[0],
        DocumentKind.AUTOMATION,
    )
    section = next(
        section for section in missing.sections if section.id == "capability-renovate"
    )
    assert section.rows[0].state == "gap"  # A name cannot establish a capability.


def test_sparse_and_empty_snapshots_expose_gaps_without_fabricating_topology() -> None:
    snapshot, _ = reconcile(CollectionResult())
    for doc in render_documents(snapshot).documents:
        assert doc.generation_state == "incomplete_estate"
        assert doc.publication_blocked
        assert not doc.sources
        assert all(row.state == "gap" for row in rows(doc))
        assert "No source facts are available" in doc.to_markdown()
    sparse, _ = reconcile(complete(observation("architecture.node", node("api"))))
    deployment = document(sparse, DocumentKind.DEPLOYMENT)
    for stage in (
        "validation",
        "candidate",
        "runtime-gate",
        "release",
        "promotion",
        "deployment",
        "rollback",
        "verification",
    ):
        assert (
            next(section for section in deployment.sections if section.id == stage)
            .rows[0]
            .state
            == "gap"
        )
    catalog = document(sparse, DocumentKind.CATALOG)
    assert any(
        row.value == "Repository purpose is not declared." for row in rows(catalog)
    )


def test_drift_ambiguity_and_conflicting_relationships_keep_all_candidates() -> None:
    config = observation("architecture.node", node("api", host="linux"))
    prose = observation(
        "architecture.node",
        node("api", host="old"),
        "README.md",
        Authority.DOCUMENTATION,
    )
    drift, _ = reconcile(
        complete(
            config,
            prose,
            observation("architecture.edge", edge(EdgeKind.HOSTED_BY, "linux")),
            observation(
                "architecture.edge",
                edge(EdgeKind.HOSTED_BY, "old"),
                "README.md",
                Authority.DOCUMENTATION,
            ),
        )
    )
    catalog = document(drift, DocumentKind.CATALOG)
    candidates = [row for row in rows(catalog) if row.label == "api / host"]
    assert {row.value for row in candidates} == {"linux", "old"}
    assert any(row.value == "linux" and "preferred" in row.state for row in candidates)
    assert any(row.value == "old" and "alternative" in row.state for row in candidates)
    relation_rows = [row for row in rows(catalog) if row.label == "hosted_by"]
    assert any("preferred" in row.state for row in relation_rows)
    assert any("alternative" in row.state for row in relation_rows)
    peer = observation("architecture.node", node("api", host="nas"), "other.toml")
    ambiguous, _ = reconcile(complete(config, prose, peer))
    for doc in render_documents(ambiguous).documents:
        assert doc.publication_blocked
        assert doc.generation_state == "blocked"
    runtime = document(ambiguous, DocumentKind.RUNTIME)
    assert {row.value for row in rows(runtime) if row.label == "api / host"} == {
        "linux",
        "old",
        "nas",
    }
    assert "no preferred value" in runtime.to_markdown()


def test_partial_sources_preserve_stale_content_and_block_publication() -> None:
    original = observation("architecture.node", node("api", host="linux"))
    first, _ = reconcile(complete(original))
    partial, _ = reconcile(
        CollectionResult(
            failures=(Failure("fixture", original.provenance, "parse_error"),)
        ),
        first,
    )
    for doc in render_documents(partial).documents:
        assert doc.publication_blocked
        assert doc.generation_state == "incomplete_estate"
        assert "Source verification failure" in doc.to_markdown()
    runtime = document(partial, DocumentKind.RUNTIME)
    assert any(row.value == "linux" and "stale" in row.state for row in rows(runtime))
    assert any(source.verification == "stale" for source in runtime.sources)
    assert "Prior known-good evidence retained" in runtime.to_markdown()


def test_rendering_is_deterministic_across_input_order_and_restarts() -> None:
    collection = collect(registry(), FixtureGitHub().client())
    first, _ = reconcile(collection)
    second, _ = reconcile(
        replace(collection, observations=tuple(reversed(collection.observations))),
        first,
    )
    assert first.id == second.id
    assert render_documents(first).to_json() == render_documents(second).to_json()
    assert [doc.to_markdown() for doc in render_documents(first).documents] == [
        doc.to_markdown() for doc in render_documents(second).documents
    ]


def test_transient_and_provenance_changes_do_not_change_semantic_document_hashes() -> (
    None
):
    original = observation(
        "architecture.node", node("api", collected_at="t1", host="linux")
    )
    first, _ = reconcile(complete(original), policy=Policy())
    updated = replace(
        original,
        value=node("api", collected_at="t2", host="linux"),
        provenance=replace(original.provenance, revision="c" * 40, blob="d" * 40),
    )
    second, _ = reconcile(complete(updated), first)
    left, right = render_documents(first), render_documents(second)
    assert left.snapshot_id != right.snapshot_id
    assert [doc.content_hash for doc in left.documents] == [
        doc.content_hash for doc in right.documents
    ]
    assert "collected_at" not in left.to_json()
    assert left.documents[0].sources != right.documents[0].sources


def test_renderer_uses_graph_and_provenance_without_collecting_or_reinterpreting_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = representative()
    injected = replace(
        snapshot,
        collection=replace(
            snapshot.collection,
            observations=(
                observation("architecture.node", "fixture-sensitive-literal"),
            ),
        ),
    )

    def forbidden(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail(
            "Renderer must not read files, collect sources or normalize evidence"
        )

    monkeypatch.setattr(Path, "read_text", forbidden)
    monkeypatch.setattr("architecture_docs.collection.collect", forbidden)
    monkeypatch.setattr("architecture_docs.graph.normalize", forbidden)
    rendered = render_documents(injected)
    assert "fixture-sensitive-literal" not in rendered.to_json()
    original = render_documents(snapshot)
    for current, prior in zip(rendered.documents, original.documents, strict=True):
        assert current.generation_state == "incomplete_estate"
        assert tuple(
            section
            for section in current.sections
            if section.id not in {"estate-coverage", "supporting-contracts", "summary"}
        ) == tuple(
            section
            for section in prior.sections
            if section.id not in {"estate-coverage", "supporting-contracts", "summary"}
        )


def test_markdown_escaping_and_immutable_artifact_configuration() -> None:
    assert (
        escape("a|b <script> [link](url)\n#title")
        == r"a\|b &lt;script&gt; \[link\]\(url\) \#title"
    )
    config = RenderConfig("Team <script> | ", False)
    documents = render_documents(representative(), config)
    text = documents.documents[0].to_markdown(config)
    assert text.startswith(r"# Team &lt;script&gt; \| Architecture Overview")
    assert "https://github.com/" not in text
    with pytest.raises(ValueError, match="configuration mismatch"):
        documents.documents[0].to_markdown()
    for bad in ("line\nbreak", "x" * 101):
        with pytest.raises(ValueError, match="invalid document title prefix"):
            RenderConfig(bad)
    with pytest.raises(ValueError, match="invalid source-link configuration"):
        RenderConfig(include_source_links="yes")  # type: ignore[arg-type]


def test_sources_retain_pinned_commits_blob_authority_and_safe_links() -> None:
    item = observation(
        "package.name", "api", source="folder with spaces/pyproject.toml"
    )
    source = SourceReference.from_evidence(Evidence(item))
    assert source.revision == "a" * 40
    assert source.blob == "b" * 40
    assert source.authority == "configuration"
    assert "folder%20with%20spaces/pyproject.toml" in source_url(source)
    assert (
        source_url(replace(source, source="github:repository"))
        == "https://github.com/fixture/service"
    )
    assert (
        source_url(replace(source, revision=None))
        == "https://github.com/fixture/service"
    )


def test_future_snapshot_or_graph_schema_is_rejected() -> None:
    snapshot = representative()
    for future in (
        replace(snapshot, schema_version=2),
        replace(snapshot, graph=replace(snapshot.graph, schema_version=2)),
    ):
        with pytest.raises(ValueError, match="unsupported rendering model schema"):
            render_documents(future)
