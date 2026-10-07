"""Architecture quality changes preserve evidence, uncertainty and stable hashes."""

from dataclasses import replace
from pathlib import Path

from architecture_docs.collection import collect
from architecture_docs.collectors.content import configuration
from architecture_docs.collectors.contracts import SourceFile
from architecture_docs.declarations import EdgeKind, NodeKind, canonical
from architecture_docs.model import CollectionResult, Failure, Provenance
from architecture_docs.reconciliation import reconcile
from architecture_docs.renderers import render_documents
from architecture_docs.renderers.artifacts import DocumentKind
from test_collection import FixtureGitHub, registry
from test_reconciliation import complete, node, observation
from test_rendering import document, representative, rows


def test_overview_excludes_inventory_and_reference_only_dependencies() -> None:
    additions = (
        observation("workflow.uses", "actions/checkout@v4", ".github/workflows/ci.yml"),
        observation("workflow.job", "test", ".github/workflows/ci.yml"),
        observation("package.dependency", "pytest", "pyproject.toml"),
        observation("compose.volumes", "temporary-volume", "compose.yml"),
        observation(
            "architecture.node",
            node("ledger", NodeKind.STORAGE, technology="postgresql"),
        ),
        observation(
            "architecture.edge",
            canonical(
                {
                    "kind": EdgeKind.USES_STORAGE,
                    "source": {"kind": "service", "name": "api"},
                    "target": {"kind": "storage", "name": "ledger"},
                }
            ),
        ),
    )
    original = representative()
    snapshot, _ = reconcile(
        replace(
            original.collection,
            observations=original.collection.observations + additions,
        )
    )
    overview = document(snapshot, DocumentKind.OVERVIEW)
    fact_text = " ".join(row.label + row.value for row in rows(overview))
    diagram = next(
        section.diagram for section in overview.sections if section.id == "boundaries"
    )
    assert diagram is not None
    for absent in (
        "actions/checkout",
        "pytest",
        "temporary-volume",
        "ci.yml",
        "reference only",
    ):
        assert absent not in fact_text
        assert absent not in diagram
    for present in (
        "ledger",
        "postgresql",
        "analytics",
        "dagster",
        "fixture/infrastructure",
    ):
        assert present in fact_text
    catalog = document(snapshot, DocumentKind.CATALOG).to_markdown()
    for present in ("actions/checkout", "pytest", "temporary-volume", "ci.yml"):
        assert present in catalog


def test_summaries_precede_details_and_catalog_summarizes_each_repository() -> None:
    snapshot = representative()
    for doc in render_documents(snapshot).documents:
        assert doc.sections[0].id == "summary"
        assert len(doc.sections[0].rows) == 1
        assert doc.to_markdown().index("## Summary") < doc.to_markdown().index(
            "## Estate coverage"
        )
    catalog = document(snapshot, DocumentKind.CATALOG)
    for section in catalog.sections:
        if section.title.startswith("fixture/"):
            assert section.rows[0].label == "Repository summary"
            assert "Purpose:" in section.rows[0].value
            assert section.rows[0].sources
    # Only the catalog repeats full successful estate inventory.
    for doc in render_documents(snapshot).documents:
        coverage = next(
            section for section in doc.sections if section.id == "estate-coverage"
        )
        if doc.id != DocumentKind.CATALOG:
            assert not any(
                row.id.startswith("estate:repository:") and row.state == "verified"
                for row in coverage.rows
            )


def test_reference_gaps_are_grouped_by_domain_with_provenance() -> None:
    snapshot, _ = reconcile(
        complete(
            observation(
                "workflow.uses", "actions/checkout@v4", ".github/workflows/a.yml"
            ),
            observation("workflow.uses", "actions/cache@v4", ".github/workflows/b.yml"),
        )
    )
    catalog = document(snapshot, DocumentKind.CATALOG)
    diagnostics = [
        section
        for section in catalog.sections
        if section.id.startswith("evidence-status:")
    ]
    refs = [
        row
        for section in diagnostics
        for row in section.rows
        if row.value.startswith("reference only:")
    ]
    assert len(refs) == 2  # Repository dependencies and workflow identities.
    assert all(len(row.entities) == 2 and row.sources for row in refs)
    assert {row.label for row in refs} == {
        "Repository dependencies",
        "Automation and orchestration",
    }


def test_identical_secret_names_group_consumers_without_merging_locations() -> None:
    observations = tuple(
        observation(
            "architecture.node",
            node(
                "SHARED_TOKEN",
                NodeKind.SECRET_REFERENCE,
                project="platform",
                path=path,
            ),
            repository=repository,
        )
        for repository, path in (("fixture/api", "/api"), ("fixture/worker", "/worker"))
    ) + tuple(
        observation(
            "workflow.secret_name",
            "SHARED_TOKEN",
            ".github/workflows/release.yml",
            repository=repository,
        )
        for repository in ("fixture/api", "fixture/worker")
    )
    baseline = representative().collection
    added = complete(*observations)
    collection = replace(
        baseline,
        observations=baseline.observations + observations,
        coverage=baseline.coverage + added.coverage,
    )
    snapshot, _ = reconcile(collection)
    doc = document(snapshot, DocumentKind.SECRETS)
    group = next(
        section for section in doc.sections if section.id == "secret-mappings:external"
    )
    references = [row for row in group.rows if row.label == "SHARED_TOKEN"]
    assert len(references) == 2
    assert {source.repository for row in references for source in row.sources} == {
        "fixture/api",
        "fixture/worker",
    }
    paths = [row for row in rows(doc) if row.label == "SHARED_TOKEN / path"]
    assert {row.value for row in paths} == {"/api", "/worker"}
    assert all(len(row.entities) == 1 for row in paths)
    assert "Secret owner is not declared" not in doc.to_markdown()
    assert all(
        any(source.source == ".github/workflows/release.yml" for source in row.sources)
        for row in references
    )
    assert not doc.publication_blocked  # Different consumer locations are not conflict.
    assert (
        document(
            reconcile(
                replace(
                    collection, observations=tuple(reversed(collection.observations))
                )
            )[0],
            DocumentKind.SECRETS,
        ).to_json()
        == doc.to_json()
    )


def test_runbook_support_identifies_missing_procedure_contract_without_commands() -> (
    None
):
    snapshot = representative()
    doc = document(snapshot, DocumentKind.ROLLBACK_RUNBOOK)
    support = next(
        section for section in doc.sections if section.id == "supporting-contracts"
    )
    assert any(
        "scripts/restore.sh" in row.label + row.value and row.sources
        for row in support.rows
    )
    assert "phase, order" in support.rows[0].value
    assert "same revision" in support.rows[0].value
    assert not any(row.state == "grounded reference" for row in support.rows)
    assert "No evidenced" in doc.to_markdown()


def test_repository_ledger_contracts_declare_postgresql() -> None:
    source = SourceFile(
        Provenance("SpencerRWood/architecture-docs", "architecture.toml", None),
        Path("architecture.toml").read_text(),
    )
    facts = configuration(source)
    for name in ("snapshot-ledger", "publication-ledger"):
        assert (
            "architecture.node",
            node(name, NodeKind.STORAGE, technology="postgresql"),
        ) in facts
    assert not any('"technology":"sqlite"' in value for _, value in facts)


def test_specialized_views_leave_package_details_in_catalog() -> None:
    snapshot, _ = reconcile(complete(observation("package.dependency", "pytest")))
    automation = document(snapshot, DocumentKind.AUTOMATION)
    assert not any("pytest" in row.value for row in rows(automation))
    assert "pytest" in document(snapshot, DocumentKind.CATALOG).to_markdown()


def test_runbook_candidates_show_existing_sources_and_withhold_applicability() -> None:
    collection = collect(registry(), FixtureGitHub().client())
    snapshot, _ = reconcile(collection)
    doc = document(snapshot, DocumentKind.DEPLOYMENT_RUNBOOK)
    candidates = [row for row in rows(doc) if row.id.startswith("candidate-sources:")]
    assert len(candidates) == 1
    assert "scripts/deploy.sh" in candidates[0].label
    assert "applicability to this runbook is not declared" in doc.to_markdown()
    assert "phase, order" in doc.to_markdown()
    assert candidates[0].state == "gap"
    assert candidates[0].sources
    assert not any(row.state == "grounded reference" for row in rows(doc))
    source = next(
        item
        for item in collection.observations
        if item.provenance.source == "scripts/deploy.sh"
    )
    stale, _ = reconcile(
        CollectionResult(
            failures=(
                Failure(
                    "executable",
                    source.provenance,
                    "parse_error",
                ),
            )
        ),
        snapshot,
    )
    assert not any(
        row.id.startswith("candidate-sources:")
        for row in rows(document(stale, DocumentKind.DEPLOYMENT_RUNBOOK))
    )
