"""Repository fixtures and adversarial failures exercise reconciliation semantics."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from architecture_docs.codec import collection_from_json, snapshot_from_json
from architecture_docs.collection import collect
from architecture_docs.declarations import EdgeKind, NodeKind, canonical
from architecture_docs.graph import Node
from architecture_docs.model import (
    Authority,
    CollectionResult,
    Failure,
    Observation,
    Provenance,
    RepositoryInventory,
    SourceCoverage,
)
from architecture_docs.reconciliation import Policy, Tombstone, load_policy, reconcile
from estate_fixtures import FIXTURE_ESTATE
from test_collection import FixtureGitHub, registry


def observation(
    key: str,
    value: str,
    source: str = "architecture.toml",
    authority: Authority = Authority.CONFIGURATION,
    repository: str = "fixture/service",
) -> Observation:
    return Observation(
        "fixture",
        authority,
        key,
        value,
        Provenance(repository, source, "a" * 40, "b" * 40),
    )


def node(name: str, kind: NodeKind = NodeKind.SERVICE, **attributes: str) -> str:
    return canonical({"kind": kind, "name": name, "attributes": attributes})


def edge(kind: EdgeKind, target: str, target_kind: NodeKind = NodeKind.HOST) -> str:
    return canonical(
        {
            "kind": kind,
            "source": {"kind": "service", "name": "api"},
            "target": {"kind": target_kind, "name": target},
        }
    )


def complete(*observations: Observation) -> CollectionResult:
    return CollectionResult(
        (*observations, FIXTURE_ESTATE.observation()),
        coverage=tuple(
            {
                SourceCoverage(
                    item.provenance.repository,
                    item.provenance.source,
                    item.collector,
                    item.provenance.revision,
                )
                for item in observations
            }
        ),
    )


def find_node(nodes: tuple[Node, ...], name: str) -> Node:
    return next(item for item in nodes if item.name == name)


def test_representative_collectors_feed_all_r1_graph_types() -> None:
    fixture = FixtureGitHub()
    result = collect(registry(), fixture.client())
    assert result.complete
    snapshot, diff = reconcile(result)
    assert {item.kind for item in snapshot.graph.nodes} == set(NodeKind)
    assert {item.kind for item in snapshot.graph.edges} == set(EdgeKind)
    assert diff.material
    assert not snapshot.publication_blocked
    assert snapshot.graph.cross_repository_edges()
    assert all(item.evidence for item in snapshot.graph.nodes)
    assert all(item.evidence for item in snapshot.graph.edges)
    secret = find_node(snapshot.graph.manifest(NodeKind.SECRET_REFERENCE), "API_TOKEN")
    assert {prop.name for prop in secret.properties} >= {
        "project",
        "environment",
        "path",
        "injection",
    }
    assert "fixture-sensitive-literal" not in snapshot.to_json()
    assert snapshot_from_json(snapshot.to_json()) == snapshot
    assert collection_from_json(result.to_json()) == result


def test_conflicts_preserve_precedence_and_equal_authority_ambiguity() -> None:
    config = observation("architecture.node", node("api", host="linux"))
    prose = observation(
        "architecture.node",
        node("api", host="old"),
        "README.md",
        Authority.DOCUMENTATION,
    )
    snapshot, _ = reconcile(complete(config, prose))
    prop = find_node(snapshot.graph.nodes, "api").properties[0]
    assert prop.state == "drift"
    assert prop.preferred == "linux"
    assert {candidate.value for candidate in prop.candidates} == {"linux", "old"}
    peer = observation("architecture.node", node("api", host="other"), "other.toml")
    ambiguous, _ = reconcile(complete(config, prose, peer))
    prop = find_node(ambiguous.graph.nodes, "api").properties[0]
    assert prop.state == "ambiguous"
    assert prop.preferred is None
    assert ambiguous.publication_blocked
    assert len(prop.candidates) == 3


def test_conflicting_host_relationships_are_not_silently_selected() -> None:
    snapshot, _ = reconcile(
        complete(
            observation("architecture.edge", edge(EdgeKind.HOSTED_BY, "linux")),
            observation(
                "architecture.edge", edge(EdgeKind.HOSTED_BY, "nas"), "other.toml"
            ),
        )
    )
    assert len(snapshot.graph.edges) == 2
    prop = find_node(snapshot.graph.nodes, "api").properties[0]
    assert prop.name == "relationship:hosted_by"
    assert prop.state == "ambiguous"


def test_no_change_and_deterministic_order_duplicates_and_revision_churn() -> None:
    fixture = FixtureGitHub()
    collection = collect(registry(), fixture.client())
    first, _ = reconcile(collection)
    shuffled = replace(
        collection,
        observations=tuple(reversed(collection.observations)) + collection.observations,
        coverage=tuple(reversed(collection.coverage)),
        inventories=tuple(reversed(collection.inventories)),
    )
    second, diff = reconcile(shuffled, first)
    assert second.id == first.id
    assert not diff.material
    changed_revision = replace(
        collection,
        observations=tuple(
            replace(
                item,
                provenance=replace(item.provenance, revision="c" * 40),
            )
            if item.collector != "estate"
            else item
            for item in collection.observations
        ),
        coverage=tuple(
            replace(item, revision="c" * 40) for item in collection.coverage
        ),
        inventories=tuple(
            replace(item, revision="c" * 40) for item in collection.inventories
        ),
    )
    updated, diff = reconcile(changed_revision, first)
    assert updated.id != first.id
    assert not diff.material


def test_configured_transient_fields_do_not_mask_semantic_change() -> None:
    policy = Policy(("collected_at", "tag"))
    first, _ = reconcile(
        complete(
            observation(
                "architecture.node",
                node("api", collected_at="t1", tag="v1", host="linux"),
            )
        ),
        policy=policy,
    )
    second, diff = reconcile(
        complete(
            observation(
                "architecture.node",
                node("api", collected_at="t2", tag="v2", host="linux"),
            )
        ),
        first,
        policy,
    )
    assert not diff.material
    _, diff = reconcile(
        complete(
            observation(
                "architecture.node",
                node("api", collected_at="t2", tag="v2", host="nas"),
            )
        ),
        second,
        policy,
    )
    assert diff.material


@pytest.mark.parametrize(
    ("key", "value", "replacement", "category"),
    [
        (
            "architecture.node",
            node("platform", NodeKind.SYSTEM),
            node("next", NodeKind.SYSTEM),
            "system",
        ),
        (
            "architecture.edge",
            edge(EdgeKind.DEPENDS_ON, "upstream", NodeKind.SERVICE),
            edge(EdgeKind.DEPENDS_ON, "next", NodeKind.SERVICE),
            "dependency",
        ),
        (
            "architecture.edge",
            edge(EdgeKind.DEPLOYS_TO, "dev", NodeKind.ENVIRONMENT),
            edge(EdgeKind.DEPLOYS_TO, "prod", NodeKind.ENVIRONMENT),
            "deployment_path",
        ),
        (
            "architecture.node",
            node("api", deployment_path="compose.yml"),
            node("api", deployment_path="new-compose.yml"),
            "deployment_path",
        ),
        (
            "architecture.edge",
            edge(EdgeKind.READS_DATABASE, "db", NodeKind.DATABASE),
            edge(EdgeKind.READS_DATABASE, "next", NodeKind.DATABASE),
            "data_storage",
        ),
        (
            "architecture.edge",
            edge(EdgeKind.USES_STORAGE, "data", NodeKind.STORAGE),
            edge(EdgeKind.USES_STORAGE, "nas", NodeKind.STORAGE),
            "data_storage",
        ),
        (
            "architecture.edge",
            edge(EdgeKind.CONSUMES_SECRET, "TOKEN", NodeKind.SECRET_REFERENCE),
            edge(EdgeKind.CONSUMES_SECRET, "OTHER", NodeKind.SECRET_REFERENCE),
            "secret_topology",
        ),
        ("release.publish", "true", "false", "release_contract"),
        ("shell.executable", "docker", "ansible", "runbook"),
        (
            "architecture.node",
            node("deploy", NodeKind.PROCEDURE, verification="check.sh"),
            node("deploy", NodeKind.PROCEDURE, verification="check-v2.sh"),
            "runbook",
        ),
    ],
)
def test_material_change_categories(
    key: str, value: str, replacement: str, category: str
) -> None:
    first, _ = reconcile(complete(observation(key, value)))
    _, diff = reconcile(complete(observation(key, replacement)), first)
    assert diff.material
    assert category in {change.category for change in diff.changes}


@pytest.mark.parametrize(
    "reason",
    ["parse_error", "access_or_rate_limit", "incomplete_tree", "collector_error"],
)
def test_partial_failures_preserve_known_good_and_allow_healthy_peer_update(
    reason: str,
) -> None:
    original = observation("architecture.node", node("api", host="linux"))
    peer = observation(
        "architecture.node", node("peer", host="old"), repository="fixture/peer"
    )
    first, _ = reconcile(complete(original, peer))
    failure = Failure("fixture", original.provenance, reason)
    healthy = replace(peer, value=node("peer", host="new"))
    incomplete = replace(complete(healthy), failures=(failure,))
    snapshot, diff = reconcile(incomplete, first)
    retained = next(item for item in snapshot.evidence if item.observation == original)
    assert retained.verification == "stale"
    assert snapshot.publication_blocked
    assert find_node(snapshot.graph.nodes, "api").properties[0].preferred == "linux"
    assert find_node(snapshot.graph.nodes, "peer").properties[0].preferred == "new"
    assert not any(change.operation == "removed" for change in diff.changes)
    recovered, _ = reconcile(complete(original, healthy), snapshot)
    assert not recovered.publication_blocked


def test_incomplete_plugin_payload_cannot_replace_prior_source() -> None:
    original = observation("architecture.node", node("api", host="linux"))
    first, _ = reconcile(complete(original))
    partial = replace(
        complete(replace(original, value=node("api", host="broken"))),
        failures=(Failure("fixture", original.provenance, "parse_error"),),
    )
    retained, diff = reconcile(partial, first)
    assert {change.category for change in diff.changes} == {"estate_coverage"}
    retained_sources = tuple(
        item for item in retained.evidence if item.observation.collector != "estate"
    )
    original_sources = tuple(
        item for item in first.evidence if item.observation.collector != "estate"
    )
    assert retained_sources == tuple(
        replace(item, verification="stale") for item in original_sources
    )


def test_absence_without_positive_coverage_or_narrowed_scope_is_not_deletion() -> None:
    original = observation("architecture.node", node("api"))
    first, _ = reconcile(complete(original))
    for absent in (
        CollectionResult(),
        CollectionResult(skipped=("fixture/service",)),
        CollectionResult(
            inventories=(
                RepositoryInventory("fixture/service", "c" * 40, (), ("README.md",)),
            )
        ),
    ):
        snapshot, diff = reconcile(absent, first)
        assert snapshot.evidence
        assert {change.category for change in diff.changes} == {"estate_coverage"}
        assert snapshot.publication_blocked


def test_positive_source_replacement_tree_absence_and_explicit_tombstone() -> None:
    original = observation("architecture.node", node("api"))
    first, _ = reconcile(complete(original))
    for absent in (
        CollectionResult(coverage=complete(original).coverage),
        CollectionResult(
            inventories=(RepositoryInventory("fixture/service", "c" * 40, (), ("*",)),)
        ),
    ):
        snapshot, diff = reconcile(absent, first)
        assert not snapshot.evidence
        assert {
            change.operation
            for change in diff.changes
            if change.category != "estate_coverage"
        } == {"removed"}
        assert snapshot.estate_state == "incomplete_estate"
        assert snapshot.publication_blocked
    snapshot, diff = reconcile(
        CollectionResult(), first, Policy(tombstones=(Tombstone("fixture/service"),))
    )
    assert not snapshot.graph.nodes
    assert "repository" in {change.category for change in diff.changes}


def test_file_failure_and_repository_failure_override_inventory_absence() -> None:
    original = observation("architecture.node", node("api"))
    first, _ = reconcile(complete(original))
    inventory = RepositoryInventory("fixture/service", "c" * 40, (), ("*",))
    for failure in (
        Failure("github_content", original.provenance, "file_too_large"),
        Failure(
            "github_source",
            Provenance("fixture/service", "github:tree", None),
            "incomplete_tree",
        ),
    ):
        retained, _ = reconcile(
            CollectionResult(failures=(failure,), inventories=(inventory,)), first
        )
        assert retained.evidence[0].verification == "stale"


def test_endpoint_coverage_removes_absent_github_objects_only_after_success() -> None:
    original = observation(
        "github.tag_name", "v1", "github:releases/20", Authority.GITHUB
    )
    first, _ = reconcile(complete(original))
    scope = SourceCoverage("fixture/service", "github:releases", "fixture", None)
    absent, _ = reconcile(CollectionResult(coverage=(scope,)), first)
    assert not absent.evidence
    failed, _ = reconcile(
        CollectionResult(
            failures=(
                Failure(
                    "fixture",
                    Provenance("fixture/service", "github:releases", None),
                    "pagination_limit",
                ),
            ),
            coverage=(scope,),
        ),
        first,
    )
    assert failed.evidence[0].verification == "stale"


def test_missing_secret_metadata_is_a_gap_without_inference() -> None:
    snapshot, _ = reconcile(
        complete(
            observation(
                "workflow.secret_name", "TOKEN", ".github/workflows/release.yml"
            )
        )
    )
    assert {
        gap.reason
        for gap in snapshot.graph.gaps
        if gap.reason.startswith("missing_secret_")
    } == {
        "missing_secret_project",
        "missing_secret_environment",
        "missing_secret_path",
        "missing_secret_injection",
    }


def test_conflicting_completeness_records_cannot_authorize_deletion() -> None:
    inventory = RepositoryInventory("fixture/service", "a" * 40, (), ("*",))
    with pytest.raises(ValueError, match="conflicting repository inventories"):
        reconcile(
            CollectionResult(
                inventories=(
                    inventory,
                    replace(inventory, paths=("architecture.toml",)),
                )
            )
        )
    coverage = SourceCoverage(
        "fixture/service", "architecture.toml", "fixture", "a" * 40
    )
    with pytest.raises(ValueError, match="conflicting source coverage"):
        reconcile(
            CollectionResult(coverage=(coverage, replace(coverage, revision="b" * 40)))
        )


def test_reconciliation_policy_is_explicit_versioned_and_rejects_unknown_fields(
    tmp_path: Path,
) -> None:
    path = tmp_path / "policy.toml"
    path.write_text(
        'version=1\ntransient_properties=["tag"]\n[[tombstones]]\nrepository="fixture/service"\nsource="old.toml"\nreason="retired"\n'
    )
    assert load_policy(path) == Policy(
        ("tag",), (Tombstone("fixture/service", "old.toml", "retired"),)
    )
    path.write_text("version=1")
    assert load_policy(path) == Policy()
    for content in (
        "version=2",
        'version=1\nsecret_value="fixture-sensitive-literal"',
        'version=1\ntransient_properties="tag"',
        'version=1\ntransient_properties=["unknown"]',
    ):
        path.write_text(content)
        with pytest.raises(ValueError, match="invalid reconciliation policy"):
            load_policy(path)


def test_bad_or_future_records_fail_closed_without_raw_exception_text() -> None:
    for payload in (
        "invalid",
        "[]",
        "null",
        '{"schema_version":1,"observations":{},"failures":[],"skipped":[]}',
        '{"schema_version":2}',
        '{"schema_version":1,"secret_value":"fixture-sensitive-literal"}',
    ):
        with pytest.raises(ValueError, match="invalid collection record"):
            collection_from_json(payload)
        with pytest.raises(ValueError, match="invalid snapshot record"):
            snapshot_from_json(payload)
    snapshot, _ = reconcile(complete(observation("architecture.node", node("api"))))
    data = json.loads(snapshot.to_json())
    data["evidence"][0]["verification"] = "unknown"
    with pytest.raises(ValueError, match="invalid snapshot record"):
        snapshot_from_json(canonical(data))
    data = json.loads(snapshot.to_json())
    data["evidence"][0]["observation"]["value"] = {"body": "fixture-sensitive-literal"}
    with pytest.raises(ValueError, match="invalid snapshot record"):
        snapshot_from_json(canonical(data))
    data = json.loads(snapshot.to_json())
    data["graph"]["nodes"] = []
    with pytest.raises(ValueError, match="invalid snapshot record"):
        snapshot_from_json(canonical(data))
