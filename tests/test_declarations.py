"""Reject unreviewed values and malformed declaration contracts at collection."""

from dataclasses import replace

import pytest

from architecture_docs.collection import collect
from architecture_docs.declarations import (
    NodeKind,
    architecture_declarations,
    edge_declaration,
    identifier,
    node_declaration,
    reference,
)
from architecture_docs.reconciliation import reconcile
from test_collection import FixtureGitHub, registry
from test_reconciliation import complete, node, observation


@pytest.mark.parametrize(
    "value",
    ["raw prose", "postgresql://user:password@host", "/../private", "../private", 123],
)
def test_identifier_rejects_bodies_credentials_and_traversal(value: object) -> None:
    with pytest.raises(ValueError, match="invalid architecture"):
        identifier(value)


@pytest.mark.parametrize(
    "data",
    [
        {
            "kind": "secret_reference",
            "name": "TOKEN",
            "secret_value": "fixture-sensitive-literal",
        },
        {
            "kind": "secret_reference",
            "name": "TOKEN",
            "attributes": {"value": "fixture-sensitive-literal"},
        },
        {"kind": "service", "name": "api", "attributes": "invalid"},
    ],
)
def test_unknown_fields_cannot_enter_node_schema(data: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="unknown"):
        node_declaration(data)


def test_invalid_edge_and_repository_references_fail_closed() -> None:
    with pytest.raises(ValueError, match="invalid edge"):
        edge_declaration({"kind": "depends_on"})
    with pytest.raises(ValueError, match="unknown architecture reference"):
        reference(
            {"kind": "host", "name": "linux", "credential": "fixture-sensitive-literal"}
        )
    with pytest.raises(ValueError, match="repository reference identity mismatch"):
        reference(
            {"kind": "repository", "name": "fixture/one", "repository": "fixture/two"}
        )
    assert (
        reference({"kind": "repository", "name": "fixture/other"})["repository"]
        == "fixture/other"
    )
    for declaration in ({"version": 2}, {"version": 1, "unknown": "field"}):
        with pytest.raises(ValueError, match="unsupported architecture declaration"):
            architecture_declarations(declaration)
    with pytest.raises(ValueError, match="repository declaration identity mismatch"):
        reconcile(
            complete(
                observation(
                    "architecture.node", node("fixture/other", kind=NodeKind.REPOSITORY)
                )
            )
        )


def test_secret_value_field_is_never_collected_persisted_or_echoed() -> None:
    fixture = FixtureGitHub()
    fixture.files["architecture.toml"] = (
        b'version=1\n[[nodes]]\nkind="secret_reference"\nname="TOKEN"\nattributes={secret_value="fixture-sensitive-literal"}'
    )
    result = collect(registry(), fixture.client())
    assert not result.complete
    assert any(
        item.reason == "parse_error" and item.provenance.source == "architecture.toml"
        for item in result.failures
    )
    snapshot, _ = reconcile(result)
    assert "fixture-sensitive-literal" not in snapshot.to_json()
    assert "secret_value" not in snapshot.to_json()


def test_script_body_change_affects_runbook_even_with_same_executable_names() -> None:
    fixture = FixtureGitHub()
    first, _ = reconcile(collect(registry(), fixture.client()))
    fixture.files["scripts/deploy.sh"] += b"\ndocker run changed-image\n"
    updated, diff = reconcile(collect(registry(), fixture.client()), first)
    assert "runbook" in {change.category for change in diff.changes}
    assert "changed-image" not in updated.to_json()
    assert "docker run" not in updated.to_json()


def test_multistage_images_are_set_membership_not_conflicting_scalar_values() -> None:
    one = observation("container.base_image", "python:3.14")
    two = replace(one, value="node:24")
    snapshot, _ = reconcile(complete(one, two))
    assert not snapshot.publication_blocked
    assert all(
        prop.state == "agreed"
        for entity in snapshot.graph.nodes
        for prop in entity.properties
    )


def test_local_workflow_references_and_compose_named_storage() -> None:
    fixture = FixtureGitHub()
    fixture.files[".github/workflows/local.yml"] = (
        b"jobs:\n  checks:\n    uses: ./.github/workflows/checks.yml\n"
    )
    fixture.files["compose.yml"] = (
        b'services:\n  api:\n    volumes: ["data:/var/data"]\nvolumes:\n  data: {}\n'
    )
    fixture.files["main.tf"] += b'\nresource "aws_instance" "worker" {}\n'
    snapshot, _ = reconcile(collect(registry(), fixture.client()))
    assert any(item.kind == "uses_storage" for item in snapshot.graph.edges)
    assert any(item.name == "aws_instance:worker" for item in snapshot.graph.nodes)
    assert any(
        item.name == "./.github/workflows/checks.yml" for item in snapshot.graph.nodes
    )
