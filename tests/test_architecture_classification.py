"""Offline classification, relationship, deployment and partial-evidence contracts."""

import json
from dataclasses import replace
from typing import Any

import pytest

from architecture_docs.architecture_graph import preferred, relationship_state
from architecture_docs.classification import (
    CAPABILITIES,
    DOMAINS,
    GROUPS,
    KINDS,
    description,
    metadata,
    role,
)
from architecture_docs.codec import snapshot_from_json
from architecture_docs.collection import collect
from architecture_docs.collectors.architecture import (
    ArchitectureCollector,
    extract,
    relation,
)
from architecture_docs.collectors.content import executable
from architecture_docs.collectors.contracts import Context, SourceFile
from architecture_docs.config import Registry, Repository
from architecture_docs.declarations import EdgeKind, NodeKind, canonical
from architecture_docs.model import (
    Authority,
    CollectionResult,
    Failure,
    Observation,
    Provenance,
    SourceCoverage,
)
from architecture_docs.reconciliation import Policy, Tombstone, reconcile
from architecture_docs.renderers import render_documents
from architecture_docs.renderers.artifacts import DocumentKind
from test_collection import REPOSITORY, REVISION, FixtureGitHub

DECLARATION = {
    "domain": "engineering",
    "capability": "developer-tooling",
    "kind": "cli",
}


def source(path: str, content: str, repository: str = REPOSITORY) -> SourceFile:
    return SourceFile(Provenance(repository, path, REVISION, "b" * 40), content)


def observations(*files: SourceFile) -> tuple[Observation, ...]:
    return tuple(
        Observation(
            ArchitectureCollector.name,
            Authority.CONFIGURATION,
            key,
            value,
            file.provenance,
        )
        for file in files
        for key, value in extract(file)
    )


def collection(*items: Observation) -> CollectionResult:
    return CollectionResult(
        items,
        coverage=tuple(
            sorted(
                {
                    SourceCoverage(
                        item.provenance.repository,
                        item.provenance.source,
                        item.collector,
                        item.provenance.revision,
                    )
                    for item in items
                }
            )
        ),
    )


def identity(repository: str) -> Observation:
    return Observation(
        "github_metadata",
        Authority.GITHUB,
        "repository.identity",
        repository,
        Provenance(repository, "github:repository", None),
    )


def project(repository: str, **fields: str) -> SourceFile:
    architecture = DECLARATION | fields
    rows = [
        "[project]",
        f'name="{repository.rsplit("/", maxsplit=1)[-1]}"',
        'description="Offline engineering component."',
        "[tool.wood.architecture]",
    ]
    rows.extend(f'{key}="{value}"' for key, value in architecture.items())
    return source("pyproject.toml", "\n".join(rows), repository)


def test_metadata_minimal_vocabularies_and_project_reuse() -> None:
    assert metadata(DECLARATION) == DECLARATION
    assert len(set(CAPABILITIES.values())) == len(GROUPS)
    assert "engineering" in DOMAINS
    assert "cli" in KINDS
    assert metadata(DECLARATION | {"solution": "synthetic-website-analytics-platform"})[
        "solution"
    ]
    facts = dict(extract(project(REPOSITORY)))
    assert facts["component.name"] == "service"
    assert facts["component.description"] == "Offline engineering component."
    assert facts["classification.state"] == "declared"
    assert extract(source("nested/pyproject.toml", '[project]\nname="nested"')) == []
    assert role("fixture/template-python-cli") == "engineering-template"
    assert role("fixture/synthetic-website-data") == "reference-solution"
    assert role("fixture/unknown") is None


@pytest.mark.parametrize(
    ("domain", "capability", "kind", "group"),
    [
        ("infrastructure", "hosting-deployment", "infrastructure-configuration", 0),
        ("platform-services", "event-messaging", "api-service", 1),
        ("platform-services", "knowledge-retrieval", "api-service", 1),
        ("engineering", "developer-tooling", "cli", 2),
        ("engineering", "agent-configuration", "configuration", 3),
        ("engineering", "agent-runtime", "library", 3),
        ("engineering", "release-promotion", "workflow-library", 4),
        ("engineering", "reliability-recovery", "orchestration-service", 4),
        ("reporting", "architecture-documentation", "orchestration-service", 5),
        ("reporting", "document-rendering", "library", 5),
        ("reporting", "operational-reporting", "orchestration-service", 5),
        ("reporting", "visualization", "library", 5),
        ("applications", "web-application", "web-application", 6),
        ("reference-solutions", "analytics-lifecycle", "solution-overview", 6),
        ("reference-solutions", "analytics-lifecycle", "dbt-project", 6),
        ("templates", "project-scaffolding", "fastapi-react-template", 7),
    ],
)
def test_merged_estate_classification_vocabulary(
    domain: str, capability: str, kind: str, group: int
) -> None:
    declaration = {"domain": domain, "capability": capability, "kind": kind}
    assert metadata(declaration) == declaration
    assert CAPABILITIES[capability] == GROUPS[group]


def test_classification_only_architecture_file_without_schema_version() -> None:
    file = source(
        "architecture.toml",
        '[project]\nname="workflows"\ndescription="Reusable release workflows."\n'
        '[architecture]\ndomain="engineering"\ncapability="release-promotion"\n'
        'kind="workflow-library"',
    )
    fixture = FixtureGitHub()
    fixture.files["architecture.toml"] = file.content.encode()
    result = collect(
        Registry((Repository(REPOSITORY, ("architecture.toml",)),)), fixture.client()
    )
    assert result.complete
    assert any(item.key == "classification.kind" for item in result.observations)
    assert any(item.key == "component.description" for item in result.observations)


@pytest.mark.parametrize(
    ("path", "body", "expected"),
    [
        (
            "environments/dev.yml",
            "---\nenvironment_name: dev\nservices:\n  api: true\n",
            "deployment.selection",
        ),
        (
            "compose/api/compose.yml",
            "---\nservices:\n  api:\n    image: ghcr.io/fixture/api:1.0\n",
            "deployment.image",
        ),
        (
            "ansible/roles/api/tasks/main.yml",
            "---\n- name: Configure API\n  ansible.builtin.debug:\n    msg: ignored\n",
            "orchestration.component",
        ),
    ],
)
def test_yaml_document_markers_preserve_authoritative_topology(
    path: str, body: str, expected: str
) -> None:
    file = source(path, body)
    result = ArchitectureCollector().collect(
        Context(FixtureGitHub().client(), REPOSITORY, REVISION, (file,), {}, 1)
    )
    assert result.complete
    assert any(item.key == expected for item in result.observations)
    assert result.coverage[0].source == path


def test_inventory_hosts_retain_aliases_without_addresses_or_credentials() -> None:
    file = source(
        "ansible/inventory/dev/hosts.yml",
        "---\nall:\n  children:\n    infrastructure_hosts:\n      hosts:\n"
        "        server:\n          ansible_host: 192.0.2.1\n"
        "          ansible_password: private-fixture-value\n",
    )
    snapshot, _ = reconcile(collection(*observations(file)))
    assert [n.name for n in snapshot.graph.manifest(NodeKind.HOST)] == ["server"]
    assert any(e.kind == EdgeKind.HOSTED_BY for e in snapshot.graph.edges)
    assert "192.0.2.1" not in snapshot.to_json()
    assert "private-fixture-value" not in snapshot.to_json()
    empty = source("ansible/inventory/prod/hosts.yml", "all:\n  hosts: {}\n")
    prod, _ = reconcile(collection(*observations(empty)))
    assert [n.name for n in prod.graph.manifest(NodeKind.ENVIRONMENT)] == ["prod"]
    assert not prod.graph.manifest(NodeKind.HOST)


@pytest.mark.parametrize(
    "value",
    [
        None,
        [],
        {},
        DECLARATION | {"domain": "unknown"},
        DECLARATION | {"capability": "unknown"},
        DECLARATION | {"kind": "unknown"},
        DECLARATION | {"solution": 123},
        DECLARATION | {"solution": "../bad"},
        DECLARATION | {"schema_version": 1},
        DECLARATION | {"relationships": []},
    ],
)
def test_metadata_rejects_invalid_values(value: Any) -> None:
    with pytest.raises(ValueError, match="invalid"):
        metadata(value)


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "x" * 301,
        "token abcdefghijklmnopqrstuvwxyz",
        "password=unsafe",
        "https://user:password@host",
        "x" * 45,
    ],
)
def test_description_excludes_sensitive_or_unbounded_prose(value: Any) -> None:
    assert description(value) is None


def test_equivalent_metadata_conflicts_and_malformed_sources() -> None:
    sidecar = source(
        "architecture.toml",
        '[architecture]\ndomain="engineering"\ncapability="reporting"\nkind="report"',
    )
    snapshot, _ = reconcile(collection(*observations(project(REPOSITORY), sidecar)))
    repository = snapshot.graph.manifest(NodeKind.REPOSITORY)[0]
    assert preferred(repository, "classification.capability") is None
    assert snapshot.publication_blocked
    only, _ = reconcile(collection(*observations(sidecar)))
    assert (
        preferred(only.graph.manifest(NodeKind.REPOSITORY)[0], "classification.group")
        == GROUPS[5]
    )
    for content in (
        "unknown=1",
        '[architecture]\nkind="cli"',
        '[tool.wood.architecture]\ndomain="wrong"',
    ):
        file = source("architecture.toml", content)
        context = Context(
            FixtureGitHub().client(), REPOSITORY, REVISION, (file,), {}, 1
        )
        result = ArchitectureCollector().collect(context)
        assert result.failures
        assert "wrong" not in result.to_json()
    context = Context(
        FixtureGitHub().client(),
        REPOSITORY,
        REVISION,
        (
            source("README.md", "private"),
            source(".github/workflows/ci.yml", "jobs: {}"),
            source("play.yml", "- hosts: all\n  roles: [api]"),
        ),
        {},
        1,
    )
    assert ArchitectureCollector().collect(context).observations == ()


def test_approved_collection_archive_and_security_boundaries() -> None:
    fixture = FixtureGitHub()
    fixture.files["pyproject.toml"] = project(REPOSITORY).content.encode()
    fixture.files["architecture.toml"] = (
        b'[architecture]\nsecret="fixture-sensitive-literal"'
    )
    fixture.files[".env"] = b"domain=engineering"
    result = collect(
        Registry((Repository(REPOSITORY, ("pyproject.toml",)),)), fixture.client()
    )
    assert result.complete
    assert any(item.key == "classification.kind" for item in result.observations)
    assert "fixture-sensitive-literal" not in result.to_json()
    assert all(request.method == "GET" for request in fixture.requests)
    assert not any(
        item.provenance.source == "architecture.toml" for item in result.observations
    )
    fixture.archived = True
    archived = collect(
        Registry((Repository(REPOSITORY, ("pyproject.toml",)),)), fixture.client()
    )
    snapshot, diff = reconcile(archived, reconcile(result)[0])
    overview = next(
        document
        for document in render_documents(snapshot).documents
        if document.id == DocumentKind.OVERVIEW
    )
    assert not any(
        row.entities
        for section in overview.sections
        if section.id.startswith("group:")
        for row in section.rows
    )
    assert any(change.category == "archival" for change in diff.changes)
    assert snapshot.graph.manifest(NodeKind.REPOSITORY)


def test_python_dependencies_lockfiles_and_confidence() -> None:
    first, second = "fixture/first", "fixture/second"
    dependency = source("pyproject.toml", project(first).content + "\n", first)
    packages = source(
        "uv.lock",
        '[[package]]\nname="second"\n'
        'source={git="https://github.com/fixture/second.git?rev=main"}',
        first,
    )
    facts = observations(dependency, project(second), packages)
    dep = Observation(
        "configuration",
        Authority.CONFIGURATION,
        "package.dependency",
        "second",
        dependency.provenance,
    )
    snapshot, _ = reconcile(collection(*facts, dep))
    cross = snapshot.graph.cross_repository_edges()
    assert len(cross) == 1
    assert relationship_state(cross[0], snapshot.graph) == "inferred"
    assert not any(node.name.startswith("{") for node in snapshot.graph.nodes)
    unresolved, _ = reconcile(collection(*observations(packages)))
    assert (
        relationship_state(
            unresolved.graph.cross_repository_edges()[0], unresolved.graph
        )
        == "unresolved"
    )
    duplicate, _ = reconcile(
        collection(
            *facts,
            *observations(
                source(
                    "architecture.toml",
                    "[project]\n"
                    'name="second"\n'
                    "[architecture]\n"
                    'domain="engineering"\ncapability="developer-tooling"\n'
                    'kind="cli"',
                    "fixture/duplicate",
                )
            ),
            dep,
        )
    )
    assert not any(
        item.observation.key == "package.dependency"
        for edge in duplicate.graph.cross_repository_edges()
        for item in edge.evidence
    )
    assert dict(
        extract(
            source(
                "poetry.lock",
                '[[package]]\nname="external"\n'
                'source={git="https://example.com/private"}',
            )
        )
    ) == {"package.locked": "external"}


def test_all_configuration_evidence_families_and_solution_flow() -> None:
    files = (
        project(
            "fixture/synthetic-website-data",
            capability="data-generation",
            kind="data-generator",
            solution="synthetic-website-analytics-platform",
        ),
        project(
            "fixture/synthetic-website-analytics",
            capability="analytics",
            kind="pipeline",
            solution="synthetic-website-analytics-platform",
        ),
        source(
            "integration.toml",
            '[[integrations]]\nrepository="fixture/synthetic-website-data"\n'
            'kind="depends_on"',
            "fixture/synthetic-website-analytics",
        ),
        source(
            "workspace.yaml",
            "load_from:\n"
            " - python_module: {module_name: analytics.definitions}\n"
            " - python_package: analytics\n"
            " - python_file: {relative_path: jobs.py}",
        ),
        source(
            "definitions.py",
            "from dagster import Definitions, AssetIn, AssetKey"
            "\nimport dagster as obj"
            "\nDefinitions()"
            "\n"
            'AssetIn(key="raw")\nAssetKey("normalized")\nobj.define_asset_job("job")\n'
            "other(123)",
        ),
        source(
            "models/schema.yml", "sources:\n - name: raw\n   tables: [{name: visits}]"
        ),
        source(
            "models/visits.sql",
            "-- {{ ref('commented') }}\n"
            "select * from {{ source('raw', 'visits') }} join "
            "{{ ref('users') }} on true",
        ),
        source(
            "target/manifest.json",
            json.dumps(
                {
                    "nodes": {
                        "model.analytics.users": {
                            "resource_type": "model",
                            "depends_on": {"nodes": ["source.raw.visits"]},
                        },
                        "test.ignored": {"resource_type": "test"},
                    },
                    "sources": {"source.raw.visits": {"resource_type": "source"}},
                }
            ),
        ),
        source(
            ".github/release.toml",
            "[release]\n"
            'promotion_workflow="fixture/workflows/.github/workflows/promote.yml@main"',
        ),
    )
    snapshot, _ = reconcile(collection(*observations(*files), identity(REPOSITORY)))
    assert snapshot.graph.edges
    assert not any(node.name == "commented" for node in snapshot.graph.nodes)
    assert any(node.name == "analytics.definitions" for node in snapshot.graph.nodes)
    rendered = render_documents(snapshot)
    assert rendered == render_documents(snapshot)
    overview = next(
        document
        for document in rendered.documents
        if document.id == DocumentKind.OVERVIEW
    )
    solution = next(
        section for section in overview.sections if section.id.startswith("solution:")
    )
    assert solution.diagram
    assert "depends_on" in solution.diagram
    assert {
        "fixture/synthetic-website-data",
        "fixture/synthetic-website-analytics",
    } <= {source.repository for row in solution.rows for source in row.sources}
    assert snapshot_from_json(snapshot.to_json()) == snapshot
    assert "source.raw.visits" in rendered.to_json()


def test_relationship_states_static_corroboration_and_group_roles() -> None:
    repository = "fixture/wood-tools"
    first, second = "fixture/one", "fixture/two"
    files = (
        source("integrations.toml", f'[[integrations]]\nrepository="{second}"', first),
        source(
            ".github/integration.toml",
            f'[[integrations]]\nrepository="{second}"',
            first,
        ),
    )
    snapshot, _ = reconcile(
        collection(
            identity(repository),
            identity("fixture/workflows"),
            identity(first),
            identity(second),
            *observations(*files),
        )
    )
    assert (
        relationship_state(snapshot.graph.cross_repository_edges()[0], snapshot.graph)
        == "verified"
    )
    assert {
        preferred(node, "classification.group")
        for node in snapshot.graph.manifest(NodeKind.REPOSITORY)
    } >= {GROUPS[2], GROUPS[4]}
    single, _ = reconcile(
        collection(identity(first), identity(second), *observations(files[0]))
    )
    assert (
        relationship_state(single.graph.cross_repository_edges()[0], single.graph)
        == "declared"
    )
    key, value = relation(
        "depends_on",
        "repository",
        first,
        "repository",
        second,
        repository=second,
        state="inferred",
    )
    inferred, _ = reconcile(
        collection(
            identity(first),
            identity(second),
            Observation(
                "fixture", Authority.CONFIGURATION, key, value, files[0].provenance
            ),
        )
    )
    assert (
        relationship_state(inferred.graph.cross_repository_edges()[0], inferred.graph)
        == "inferred"
    )
    with pytest.raises(ValueError, match="invalid integration kind"):
        extract(
            source(
                "integration.toml",
                '[[integrations]]\nrepository="fixture/second"\nkind="fake"',
            )
        )
    assert (
        extract(
            source(
                "integration.toml", '[[integrations]]\nendpoint="https://private-host"'
            )
        )
        == []
    )


def test_deployment_configuration_is_not_actual_deployment_or_health() -> None:
    files = (
        source(
            "environments/dev.yml",
            "supported_environments: [dev, prod]\n"
            "services: {api: true, disabled: false}",
        ),
        source(
            "compose.yml",
            'services:\n api: {image: "ghcr.io/fixture/api:1.0", depends_on: [db]}\n'
            ' db: {image: "${UNSAFE}"}',
        ),
    )
    snapshot, _ = reconcile(collection(*observations(*files)))
    service = next(node for node in snapshot.graph.nodes if node.name == "api")
    assert (
        preferred(service, "deployment.configured_image") == "ghcr.io/fixture/api:1.0"
    )
    assert preferred(service, "deployment.deployed_revision:dev") is None
    deployment = next(
        doc
        for doc in render_documents(snapshot).documents
        if doc.id == DocumentKind.DEPLOYMENT
    ).to_markdown()
    assert "Configured target only" in deployment
    assert "Runtime health requires independent" in deployment
    assert "environment.supported" in deployment
    assert "environment.configured" in deployment
    assert "UNSAFE" not in snapshot.to_json()


def test_deployment_receipts_verify_revision_and_preserve_unavailable_evidence() -> (
    None
):
    fixture = FixtureGitHub()
    fixture.files = {
        "deployment-receipts.json": b'{"deployments":[{"id":42,"service":"api"}]}'
    }
    fixture.overrides["/deployments/42"] = {
        "sha": REVISION,
        "environment": "prod",
        "payload": {"service": "api", "token": "fixture-sensitive-literal"},
    }
    fixture.overrides["/deployments/42/statuses"] = [{"state": "success"}]
    registry = Registry((Repository(REPOSITORY, ("deployment-receipts.json",)),))
    result = collect(registry, fixture.client())
    assert result.complete
    snapshot, _ = reconcile(result)
    service = next(node for node in snapshot.graph.nodes if node.name == "api")
    assert preferred(service, "deployment.deployed_revision:prod") == REVISION
    assert preferred(service, "runtime.health:prod") == "unavailable"
    assert any(
        relationship_state(edge, snapshot.graph) == "verified"
        for edge in snapshot.graph.edges
    )
    assert "fixture-sensitive-literal" not in snapshot.to_json()
    assert all(request.method == "GET" for request in fixture.requests)
    fixture.overrides["/deployments/42/statuses"] = [{"state": "pending"}]
    failed = collect(registry, fixture.client())
    assert not failed.complete
    retained, diff = reconcile(failed, snapshot)
    assert any(
        item.verification == "stale" and item.observation.key == "deployment.attested"
        for item in retained.evidence
    )
    assert not any(change.operation == "removed" for change in diff.changes)
    invalid_selectors: tuple[dict[str, Any], ...] = (
        {"deployments": [{}]},
        {"deployments": [{}] * 51},
        {"unknown": []},
    )
    for body in invalid_selectors:
        context = Context(
            fixture.client(),
            REPOSITORY,
            REVISION,
            (source("deployment-receipts.json", json.dumps(body)),),
            {},
            1,
        )
        assert ArchitectureCollector().collect(context).failures


def test_snapshot_changes_and_missing_metadata_do_not_reclassify() -> None:
    first, _ = reconcile(collection(*observations(project(REPOSITORY))))
    updated, diff = reconcile(
        collection(
            *observations(
                project(REPOSITORY, capability="documentation", kind="pipeline")
            )
        ),
        first,
    )
    assert any(change.category == "reclassification" for change in diff.changes)
    missing, diff = reconcile(
        CollectionResult(
            coverage=(
                SourceCoverage(
                    REPOSITORY, "pyproject.toml", ArchitectureCollector.name, REVISION
                ),
            )
        ),
        updated,
    )
    assert not any(change.category == "reclassification" for change in diff.changes)
    assert any(item.verification == "stale" for item in missing.evidence)
    retained, diff = reconcile(
        replace(
            collection(),
            failures=(
                Failure(
                    ArchitectureCollector.name,
                    Provenance(REPOSITORY, "pyproject.toml", REVISION),
                    "parse_error",
                ),
            ),
        ),
        first,
    )
    assert not any(change.category == "reclassification" for change in diff.changes)
    assert retained.publication_blocked
    same, diff = reconcile(collection(*observations(project(REPOSITORY))), first)
    assert not diff.material
    assert same == first
    _, diff = reconcile(
        collection(), first, Policy(tombstones=(Tombstone(REPOSITORY),))
    )
    assert any(
        change.operation == "removed" and change.category == "repository"
        for change in diff.changes
    )


def test_solution_without_flow_and_empty_dependency_views() -> None:
    snapshot, _ = reconcile(
        collection(*observations(project(REPOSITORY, solution="isolated")))
    )
    documents = render_documents(snapshot)
    overview = next(
        document
        for document in documents.documents
        if document.id == DocumentKind.OVERVIEW
    ).to_markdown()
    assert "membership alone does not establish data flow" in overview
    empty, _ = reconcile(CollectionResult())
    catalog = next(
        document
        for document in render_documents(empty).documents
        if document.id == DocumentKind.CATALOG
    ).to_markdown()
    assert "No component dependencies are evidenced" in catalog


def test_historical_projection_is_not_reinterpreted_and_corruption_fails() -> None:
    snapshot, _ = reconcile(collection(identity("fixture/wood-tools")))
    # A frozen historical record predates inferred classifications/metadata gaps.
    historical = replace(
        snapshot,
        graph=replace(
            snapshot.graph,
            nodes=tuple(replace(node, properties=()) for node in snapshot.graph.nodes),
            gaps=(),
        ),
    )
    assert snapshot_from_json(historical.to_json()) == historical
    assert snapshot_from_json(historical.to_json()).id == historical.id
    data = json.loads(historical.to_json())
    data["graph"]["nodes"][0]["evidence"][0]["observation"]["value"] = "other"
    with pytest.raises(ValueError, match="invalid snapshot record"):
        snapshot_from_json(canonical(data))
    data = json.loads(snapshot.to_json())
    data["graph"]["schema_version"] = 100
    with pytest.raises(ValueError, match="invalid snapshot record"):
        snapshot_from_json(canonical(data))
    data = json.loads(snapshot.to_json())
    data["graph"]["nodes"][0]["properties"][0]["preferred"] = "unsupported"
    with pytest.raises(ValueError, match="invalid snapshot record"):
        snapshot_from_json(canonical(data))


def test_record_boundary_rejects_unsupported_claims_without_values() -> None:
    for key, value in (
        ("classification.solution", "../private"),
        ("classification.capability", "not-a-capability"),
        ("classification.unknown", "value"),
        ("component.description", "https://private-token"),
        (
            "architecture.relationship",
            canonical(
                {
                    "kind": "depends_on",
                    "source": {"kind": "repository", "name": REPOSITORY},
                    "target": {"kind": "repository", "name": "fixture/target"},
                    "state": "verified",
                }
            ),
        ),
        ("deployment.selection", canonical({"service": "api", "password": "private"})),
        (
            "deployment.attested",
            canonical({"service": "api", "environment": "prod", "revision": REVISION}),
        ),
        ("environment.prod", "true"),
    ):
        with pytest.raises(ValueError, match=r"invalid|unsupported"):
            Observation(
                "fixture",
                Authority.CONFIGURATION,
                key,
                value,
                source("fixture.toml", "").provenance,
            )
    for field in ("domain", "capability", "kind"):
        with pytest.raises(ValueError, match="invalid architecture vocabulary"):
            metadata(DECLARATION | {field: []})


def test_supported_workflow_environments_and_infrastructure_contracts() -> None:
    facts = executable(
        source(
            ".github/workflows/deploy.yml",
            "on:\n"
            " workflow_dispatch:\n"
            "  inputs:\n"
            "   environment:\n"
            "    options: [dev, prod]\n"
            "jobs: {}",
        )
    )
    assert ("environment.supported", "dev") in facts
    file = source(
        "environments/dev.yml",
        "environment_name: dev\n"
        "services: {api: true}\n"
        "infrastructure_infisical_runtime_services:\n"
        " api:\n"
        "  compose_file: compose/api/compose.yml\n"
        "  compose_services: [api]",
    )
    assert any(key == "architecture.relationship" for key, _ in extract(file))
    context = Context(
        FixtureGitHub().client(),
        REPOSITORY,
        REVISION,
        (
            source(
                "roles/api/tasks/main.yml",
                "- name: start\n  ansible.builtin.service: {name: api}",
            ),
        ),
        {},
        1,
    )
    result = ArchitectureCollector().collect(context)
    assert result.complete
    assert result.observations
    assert result.observations[0].value == "role:api"
    # A similarly named local function is not a Dagster API.
    assert extract(source("other.py", "def Definitions(): pass\nDefinitions()")) == []


def test_deployment_changes_are_detected_only_after_complete_collection() -> None:
    first_file = source("environments/dev.yml", "services: {api: true}")
    first, _ = reconcile(collection(*observations(first_file)))
    second_file = source("environments/dev.yml", "services: {api: true, worker: true}")
    _, diff = reconcile(collection(*observations(second_file)), first)
    assert any(change.category == "deployment_path" for change in diff.changes)
    failed = replace(
        collection(*observations(second_file)),
        failures=(
            Failure(
                "fixture",
                Provenance("fixture/other", "github:tree", None),
                "incomplete_tree",
            ),
        ),
    )
    partial, diff = reconcile(failed, first)
    assert not partial.successful
    assert not any(change.category != "estate_coverage" for change in diff.changes)
