"""Estate-level presentation filters retain detailed evidence and uncertainty."""

from dataclasses import replace

from architecture_docs.declarations import EdgeKind, NodeKind, canonical
from architecture_docs.model import CollectionResult
from architecture_docs.reconciliation import Snapshot, reconcile
from architecture_docs.renderers import render_documents
from architecture_docs.renderers.artifacts import DocumentKind
from test_reconciliation import complete, node, observation
from test_rendering import document, representative, rows


def relation(  # noqa: PLR0913 - typed fixture endpoints and repository identity
    kind: EdgeKind,
    source: str,
    target: str,
    *,
    source_kind: NodeKind = NodeKind.SERVICE,
    target_kind: NodeKind = NodeKind.SERVICE,
    target_repository: str | None = None,
) -> str:
    destination = {"kind": target_kind, "name": target}
    if target_repository:
        destination["repository"] = target_repository
    return canonical(
        {
            "kind": kind,
            "source": {"kind": source_kind, "name": source},
            "target": destination,
        }
    )


def boundaries() -> Snapshot:
    facts = [
        observation("compose.services", name, "compose.yml")
        for name in (
            "audiobookshelf",
            "bookshelf-audiobooks",
            "bookshelf-ebooks",
            "calibre",
            "calibre-web",
            "code-server-init",
            "local-cache",
            "worker",
            "public-api",
            "scheduled-runtime",
            "shared-reader",
        )
    ]
    facts.extend(
        (
            observation("architecture.node", node("declared-runtime", role="major")),
            observation("architecture.node", node("proxy", technology="reverse-proxy")),
            observation(
                "architecture.node",
                node("dagster", NodeKind.ORCHESTRATION, technology="dagster"),
            ),
            observation(
                "architecture.node",
                node("public", NodeKind.INTERFACE, protocol="https"),
            ),
            observation(
                "architecture.node",
                node("shared", NodeKind.DATABASE, technology="postgresql"),
                repository="fixture/infrastructure",
            ),
            observation(
                "architecture.edge",
                relation(
                    EdgeKind.ORCHESTRATES,
                    "dagster",
                    "scheduled-runtime",
                    source_kind=NodeKind.ORCHESTRATION,
                ),
            ),
            observation(
                "architecture.edge",
                relation(
                    EdgeKind.EXPOSES,
                    "public-api",
                    "public",
                    target_kind=NodeKind.INTERFACE,
                ),
            ),
            observation(
                "architecture.edge",
                relation(
                    EdgeKind.READS_DATABASE,
                    "shared-reader",
                    "shared",
                    target_kind=NodeKind.DATABASE,
                    target_repository="fixture/infrastructure",
                ),
            ),
            observation(
                "architecture.edge",
                relation(EdgeKind.DEPENDS_ON, "worker", "local-cache"),
            ),
            observation(
                "workflow.uses", "actions/checkout@v4", ".github/workflows/ci.yml"
            ),
            observation("workflow.job", "test", ".github/workflows/ci.yml"),
            observation("package.dependency", "pytest", "pyproject.toml"),
            observation("compose.volumes", "helper-volume", "compose.yml"),
        )
    )
    return reconcile(complete(*facts))[0]


def test_semantic_service_selection_reduces_overview_without_deleting_evidence() -> (
    None
):
    snapshot = boundaries()
    before = snapshot.to_json()
    overview = document(snapshot, DocumentKind.OVERVIEW)
    diagram = next(
        section.diagram for section in overview.sections if section.id == "boundaries"
    )
    assert diagram is not None
    for absent in (
        "audiobookshelf",
        "bookshelf-",
        "calibre",
        "code-server-init",
        "local-cache",
        "/ worker",
        "actions/",
        "ci.yml",
        "pytest",
        "helper-volume",
    ):
        assert absent not in diagram
    for present in (
        "declared-runtime",
        "proxy",
        "public-api",
        "scheduled-runtime",
        "shared-reader",
        "fixture/infrastructure",
        "shared",
        "dagster",
    ):
        assert present in diagram
    assert "declares data access to" in overview.to_markdown()
    for kind in (DocumentKind.RUNTIME, DocumentKind.CATALOG):
        text = document(snapshot, kind).to_markdown()
        for leaf in (
            "audiobookshelf",
            "bookshelf-audiobooks",
            "calibre",
            "code-server-init",
        ):
            assert leaf in text
    assert snapshot.to_json() == before
    reordered = replace(
        snapshot,
        graph=replace(
            snapshot.graph,
            nodes=tuple(reversed(snapshot.graph.nodes)),
            edges=tuple(reversed(snapshot.graph.edges)),
        ),
    )
    assert document(reordered, DocumentKind.OVERVIEW).sections == overview.sections
    assert (
        document(reordered, DocumentKind.OVERVIEW).content_hash == overview.content_hash
    )


def test_overview_summary_describes_categories_coverage_and_persistence() -> None:
    snapshot = representative()
    summary = document(snapshot, DocumentKind.OVERVIEW).sections[0].rows[0].value
    for present in (
        "represented repositories",
        "core",
        "supporting",
        "Architecture categories",
        "dagster",
        "Shared persistence boundaries",
        "analytics",
        "cross-repository relationships",
        "Estate coverage",
        "material unresolved",
    ):
        assert present in summary
    for doc in render_documents(snapshot).documents:
        assert "Featured entities" not in doc.sections[0].rows[0].value
        assert "additional entities" not in doc.sections[0].rows[0].value


def workflow_collection() -> CollectionResult:
    facts = [
        observation(
            "workflow.uses",
            value,
            ".github/workflows/release.yml",
            repository=repository,
        )
        for repository in ("fixture/service", "fixture/other")
        for value in (
            "actions/checkout@v4",
            "actions/setup-python@v5",
            "docker/build-push-action@v6",
            "astral-sh/setup-uv@v5",
            "external/workflows/.github/workflows/release.yml@v1",
            "fixture/workflows/.github/workflows/release.yml@v1",
            "fixture/workflows/.github/workflows/promote-container-to-dev.yml@v3",
        )
    ]
    facts.extend(
        (
            observation("package.name", "application", "pyproject.toml"),
            observation("source.kind", "executable", "scripts/verify_release.py"),
            observation("contract.kind", "python", "scripts/verify_release.py"),
            observation("source.kind", "configuration", "ansible/playbooks/deploy.yml"),
            observation(
                "ansible.module", "ansible.builtin.copy", "ansible/playbooks/deploy.yml"
            ),
        )
    )
    return complete(
        *(
            replace(item, collector=item.value) if item.key == "source.kind" else item
            for item in facts
        )
    )


def test_first_party_assets_aggregate_consumers_and_retain_provenance() -> None:
    collection = workflow_collection()
    snapshot, _ = reconcile(collection)
    before = snapshot.to_json()
    release = document(snapshot, DocumentKind.RELEASE_RUNBOOK)
    support = next(
        section for section in release.sections if section.id == "supporting-contracts"
    )
    shared = [
        row
        for row in support.rows
        if row.label == "fixture/workflows/.github/workflows/release.yml@v1"
    ]
    assert len(shared) == 1
    assert {source.repository for source in shared[0].sources} == {
        "fixture/service",
        "fixture/other",
    }
    assert all(
        source.source == ".github/workflows/release.yml" for source in shared[0].sources
    )
    assert "fixture/other, fixture/service" in shared[0].value
    assert any("verify_release.py" in row.label for row in support.rows)
    assert any("ansible/playbooks/deploy.yml" in row.label for row in support.rows)
    assert any("promote-container-to-dev.yml@v3" in row.label for row in support.rows)
    assert "phase, order" in support.rows[0].value
    assert "same revision" in support.rows[0].value
    for row in support.rows[1:]:
        assert row.sources
        assert row.state != "grounded reference"
        for excluded in ("actions/", "docker/", "astral-sh/", "external/workflows"):
            assert excluded not in row.label + row.value
    for kind in (DocumentKind.CATALOG, DocumentKind.AUTOMATION):
        text = document(snapshot, kind).to_markdown()
        assert "actions/checkout" in text
        assert "docker/build-push-action" in text
    assert snapshot.to_json() == before
    reversed_snapshot, _ = reconcile(
        replace(collection, observations=tuple(reversed(collection.observations)))
    )
    assert (
        document(reversed_snapshot, DocumentKind.RELEASE_RUNBOOK).to_json()
        == release.to_json()
    )


def test_explicit_external_operational_reference_stays_visible_but_not_executable() -> (
    None
):
    snapshot, _ = reconcile(
        complete(
            observation(
                "architecture.node",
                node(
                    "operator-contract",
                    NodeKind.PROCEDURE,
                    runbook="release-promotion-troubleshooting",
                    phase="steps",
                    order="1",
                    reference="actions/checkout@v4",
                ),
            )
        )
    )
    release = document(snapshot, DocumentKind.RELEASE_RUNBOOK)
    assert "actions/checkout" in release.to_markdown()
    assert not any(row.state == "grounded reference" for row in rows(release))
    assert "execution withheld" in release.to_markdown()
