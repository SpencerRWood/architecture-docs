"""Secret metadata and procedure grounding from approved, offline repositories."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from architecture_docs.codec import snapshot_from_json
from architecture_docs.collection import collect
from architecture_docs.declarations import (
    SECRET_FIELDS,
    NodeKind,
    Phase,
    RunbookKind,
    canonical,
    node_declaration,
)
from architecture_docs.model import Authority, CollectionResult, Failure
from architecture_docs.reconciliation import Snapshot, reconcile
from architecture_docs.renderers import render_documents
from architecture_docs.renderers.artifacts import DocumentKind
from test_collection import FixtureGitHub, registry
from test_reconciliation import complete, node, observation
from test_rendering import document, rows


def operational_fixture() -> FixtureGitHub:
    fixture = FixtureGitHub()
    declarations = []
    for kind in RunbookKind:
        for index, phase in enumerate(Phase, 1):
            declarations.append(
                f'\n[[nodes]]\nkind="procedure"\nname="{kind}-{phase}"\n'
                f'attributes={{runbook="{kind}", phase="{phase}", order="{index}", '
                'script="scripts/deploy.sh"}\n'
            )
    fixture.files["architecture.toml"] += "".join(declarations).encode()
    return fixture


def operational_snapshot() -> Snapshot:
    return reconcile(collect(registry(), operational_fixture().client()))[0]


def test_manifest_metadata_consumers_provenance_and_gaps() -> None:
    snapshot = operational_snapshot()
    doc = document(snapshot, DocumentKind.SECRETS)
    secret_rows = rows(doc)
    for field, value in {
        "project": "platform",
        "environment": "dev",
        "path": "/api",
        "injection": "infisical",
        "scope": "runtime",
        "required": "true",
    }.items():
        row = next(row for row in secret_rows if row.label == f"API_TOKEN / {field}")
        assert row.value == value
        assert row.sources
        assert all(source.revision for source in row.sources)
    assert any(
        row.label == "consumes_secret" and "api" in row.value for row in secret_rows
    )
    assert any(row.state == "gap" and "owner" in row.value for row in secret_rows)
    assert "missing secret project" in doc.to_markdown()
    assert all(source.revision for source in doc.sources)
    assert {
        "project",
        "environment",
        "path",
        "scope",
        "injection",
        "required",
        "owner",
    } == SECRET_FIELDS


@pytest.mark.parametrize(
    "field",
    [
        "value",
        "secret_value",
        "password",
        "credential",
        "token",
        "script",
        "purpose",
        "endpoint",
    ],
)
def test_manifest_model_rejects_value_and_unrelated_fields(field: str) -> None:
    with pytest.raises(ValueError, match="unknown"):
        node_declaration(
            {
                "kind": "secret_reference",
                "name": "TOKEN",
                "attributes": {field: "fixture-sensitive-literal"},
            }
        )


@pytest.mark.parametrize(
    "attributes",
    [
        {"required": "fixture-sensitive-literal"},
        {"path": "relative/path"},
        {"project": "https://user:password@host"},
    ],
)
def test_secret_metadata_is_validated(attributes: dict[str, str]) -> None:
    with pytest.raises(ValueError, match="invalid"):
        node_declaration(
            {"kind": "secret_reference", "name": "TOKEN", "attributes": attributes}
        )


def test_workflow_discovers_reference_names_without_aliases_literals_or_values() -> (
    None
):
    fixture = FixtureGitHub()
    fixture.files[".github/workflows/release.yml"] = b"""jobs:
  release:
    secrets:
      ALIAS: ${{ secrets.ACTUAL_TOKEN }}
      LITERAL: fixture-sensitive-literal
    steps:
      - env:
          TOKEN: ${{ secrets.RUNTIME_TOKEN }}
        run: echo fixture-sensitive-literal
"""
    result = collect(registry(), fixture.client())
    assert {
        item.value for item in result.observations if item.key == "workflow.secret_name"
    } == {"ACTUAL_TOKEN", "RUNTIME_TOKEN"}
    snapshot, _ = reconcile(result)
    for payload in (
        result.to_json(),
        snapshot.to_json(),
        render_documents(snapshot).to_json(),
    ):
        assert "fixture-sensitive-literal" not in payload
        assert "ALIAS" not in payload
        assert "LITERAL" not in payload
    assert all(request.method == "GET" for request in fixture.requests)


def test_all_eight_runbooks_have_grounded_ordered_phase_contracts() -> None:
    snapshot = operational_snapshot()
    for kind in RunbookKind:
        doc = document(snapshot, DocumentKind(f"runbook-{kind}"))
        assert doc.generation_state == "complete"
        for phase in Phase:
            section = next(section for section in doc.sections if section.id == phase)
            assert len(section.rows) == 1
            row = section.rows[0]
            assert row.state == "grounded reference"
            assert "scripts/deploy.sh" in row.value
            assert {source.source for source in row.sources} == {
                "architecture.toml",
                "scripts/deploy.sh",
            }
            assert all(
                source.revision and source.verification == "verified"
                for source in row.sources
            )
        assert "docker compose" not in doc.to_json()
        assert "fixture-sensitive-literal" not in doc.to_json()
        assert "command arguments" in doc.to_markdown()


def test_unavailable_stale_and_mismatched_sources_withhold_execution() -> None:
    fixture = operational_fixture()
    snapshot, _ = reconcile(collect(registry(), fixture.client()))
    source = next(
        item
        for item in snapshot.collection.observations
        if item.key == "source.kind" and item.provenance.source == "scripts/deploy.sh"
    )
    collections = (
        replace(
            snapshot.collection,
            observations=tuple(
                item
                for item in snapshot.collection.observations
                if item.provenance.source != "scripts/deploy.sh"
            ),
        ),
        replace(
            snapshot.collection,
            observations=tuple(
                replace(item, provenance=replace(item.provenance, revision="c" * 40))
                if item.provenance.source == "scripts/deploy.sh"
                else item
                for item in snapshot.collection.observations
            ),
        ),
        replace(
            snapshot.collection,
            failures=(Failure("executable", source.provenance, "parse_error"),),
        ),
    )
    for collection in collections:
        changed, _ = reconcile(collection)
        doc = document(changed, DocumentKind.DEPLOYMENT_RUNBOOK)
        assert not any(row.state == "grounded reference" for row in rows(doc))
        assert "execution withheld" in doc.to_markdown()
    stale, _ = reconcile(
        CollectionResult(
            failures=(Failure("github_source", source.provenance, "unavailable"),)
        ),
        snapshot,
    )
    doc = document(stale, DocumentKind.DEPLOYMENT_RUNBOOK)
    assert doc.publication_blocked
    assert not any(row.state == "grounded reference" for row in rows(doc))
    assert any(source.verification == "stale" for source in doc.sources)


def test_secret_location_drift_and_ambiguity_are_gaps_with_all_candidates() -> None:
    config = observation(
        "architecture.node",
        node("TOKEN", NodeKind.SECRET_REFERENCE, project="platform", path="/current"),
    )
    prose = observation(
        "architecture.node",
        node("TOKEN", NodeKind.SECRET_REFERENCE, path="/old"),
        "docs/contract.md",
        Authority.DOCUMENTATION,
    )
    for other in (prose, replace(prose, authority=Authority.CONFIGURATION)):
        snapshot, _ = reconcile(complete(config, other))
        doc = document(snapshot, DocumentKind.SECRETS)
        assert {row.value for row in rows(doc) if row.label == "TOKEN / path"} == {
            "/old",
            "/current",
        }
        assert "Conflicting secret path" in doc.to_markdown()
        assert doc.publication_blocked == (other.authority == Authority.CONFIGURATION)


def test_conflicting_runbook_target_is_not_an_executable_step() -> None:
    snapshot = operational_snapshot()
    declaration = next(
        item
        for item in snapshot.collection.observations
        if item.key == "architecture.node"
        and json.loads(item.value)["name"] == "deployment-redeployment-steps"
    )
    data = json.loads(declaration.value)
    data["attributes"]["script"] = "scripts/other.sh"
    updated, _ = reconcile(
        replace(
            snapshot.collection,
            observations=(
                *snapshot.collection.observations,
                replace(
                    declaration,
                    value=canonical(data),
                    provenance=replace(declaration.provenance, source="other.toml"),
                ),
            ),
        )
    )
    doc = document(updated, DocumentKind.DEPLOYMENT_RUNBOOK)
    steps = next(section for section in doc.sections if section.id == Phase.STEPS)
    assert all(row.state == "gap" for row in steps.rows)
    assert doc.publication_blocked
    assert "no preferred value" in doc.to_markdown()


@pytest.mark.parametrize(
    "updates",
    [
        {"script": "bash scripts/deploy.sh"},
        {"script": "scripts/../../private.sh"},
        {"script": "/private.sh"},
        {"script": "scripts/*.sh"},
        {"script": "scripts/notes.md"},
        {"workflow": "workflows/release.yml", "script": "scripts/deploy.sh"},
        {"script": "scripts/deploy.sh;curl"},
        {"command": "docker run fixture-sensitive-literal"},
        {"order": "0"},
        {"phase": "arbitrary"},
        {"runbook": "arbitrary"},
        {"reference": "README.md"},
    ],
)
def test_runbook_model_rejects_commands_unsafe_paths_and_ambiguous_step_shapes(
    updates: dict[str, str],
) -> None:
    attrs = {
        "runbook": "deployment-redeployment",
        "phase": "steps",
        "order": "1",
        "script": "scripts/deploy.sh",
    } | updates
    with pytest.raises(ValueError, match=r"unknown|invalid|requires|not a valid"):
        node_declaration({"kind": "procedure", "name": "deploy", "attributes": attrs})


def test_operations_are_deterministic_across_restart_and_order_changes() -> None:
    snapshot = operational_snapshot()
    restored = snapshot_from_json(snapshot.to_json())
    reversed_snapshot, _ = reconcile(
        replace(
            snapshot.collection,
            observations=tuple(reversed(snapshot.collection.observations)),
        )
    )
    assert (
        render_documents(snapshot).to_json()
        == render_documents(restored).to_json()
        == render_documents(reversed_snapshot).to_json()
    )


def test_workflow_and_documented_contract_steps_use_the_actual_source() -> None:
    fixture = operational_fixture()
    fixture.files["architecture.toml"] = fixture.files["architecture.toml"].replace(
        b'script="scripts/deploy.sh"', b'workflow=".github/workflows/release.yml"', 1
    )
    snapshot, _ = reconcile(collect(registry(), fixture.client()))
    doc = document(snapshot, DocumentKind.DEPLOYMENT_RUNBOOK)
    assert any(
        source.source == ".github/workflows/release.yml" for source in doc.sources
    )
    fixture.files["architecture.toml"] = fixture.files["architecture.toml"].replace(
        b'workflow=".github/workflows/release.yml"', b'reference="README.md"', 1
    )
    snapshot, _ = reconcile(collect(registry(), fixture.client()))
    doc = document(snapshot, DocumentKind.DEPLOYMENT_RUNBOOK)
    assert any(source.source == "README.md" for source in doc.sources)


def test_secret_fields_cannot_be_persisted_through_untrusted_snapshot_input() -> None:
    snapshot = operational_snapshot()
    payload = json.loads(snapshot.to_json())
    item = next(
        item
        for item in payload["evidence"]
        if item["observation"]["key"] == "architecture.node"
        and json.loads(item["observation"]["value"])["kind"] == "secret_reference"
    )
    declaration = json.loads(item["observation"]["value"])
    declaration["attributes"]["secret_value"] = "fixture-sensitive-literal"  # noqa: S105 - sentinel
    item["observation"]["value"] = canonical(declaration)
    with pytest.raises(ValueError, match="invalid snapshot record"):
        snapshot_from_json(canonical(payload))


def test_rendering_does_not_read_sources_or_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = operational_snapshot()

    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("Renderer must use normalized snapshot evidence only")

    monkeypatch.setattr(Path, "read_text", forbidden)
    monkeypatch.setattr("architecture_docs.collectors.github.GitHub.get", forbidden)
    assert len(render_documents(snapshot).documents) == 15


def test_inherited_and_indexed_secret_references_surface_discovery_gaps() -> None:
    fixture = FixtureGitHub()
    fixture.files[".github/workflows/release.yml"] = b"""jobs:
  inherited:
    secrets: inherit
  dynamic:
    steps:
      - run: echo ${{ secrets[inputs.secret_name] }}
"""
    snapshot, _ = reconcile(collect(registry(), fixture.client()))
    doc = document(snapshot, DocumentKind.SECRETS)
    text = doc.to_markdown()
    assert "inherited" in text
    assert "indexed" in text
    assert doc.generation_state == "with_gaps"
    assert any(row.state == "gap" and row.sources for row in rows(doc))


def test_invalid_workflow_paths_and_nonprocedure_step_fields_fail_closed() -> None:
    for target in ("workflow.yml", ".github/workflows/notes.md"):
        with pytest.raises(ValueError, match="invalid runbook workflow"):
            node_declaration(
                {
                    "kind": "procedure",
                    "name": "deploy",
                    "attributes": {
                        "runbook": "deployment-redeployment",
                        "phase": "steps",
                        "order": "1",
                        "workflow": target,
                    },
                }
            )
    with pytest.raises(ValueError, match="runbook attributes require"):
        node_declaration(
            {"kind": "service", "name": "api", "attributes": {"phase": "steps"}}
        )


def test_duplicate_order_withholds_steps_instead_of_guessing_sequence() -> None:
    fixture = operational_fixture()
    fixture.files["architecture.toml"] += b"""
[[nodes]]
kind="procedure"
name="another-deploy"
attributes={runbook="deployment-redeployment",phase="steps",order="2",script="scripts/deploy.sh"}
"""
    snapshot, _ = reconcile(collect(registry(), fixture.client()))
    doc = document(snapshot, DocumentKind.DEPLOYMENT_RUNBOOK)
    steps = next(section for section in doc.sections if section.id == Phase.STEPS)
    assert len(steps.rows) == 2
    assert all(row.state == "gap" for row in steps.rows)
    assert "Duplicate step order" in doc.to_markdown()


def test_one_repository_cannot_supply_another_repositories_missing_phases() -> None:
    snapshot = operational_snapshot()
    extra = observation(
        "architecture.node",
        node(
            "other-deploy",
            NodeKind.PROCEDURE,
            runbook="deployment-redeployment",
            phase="steps",
            order="1",
            script="scripts/deploy.sh",
        ),
        repository="fixture/other",
    )
    updated, _ = reconcile(
        replace(
            snapshot.collection,
            observations=(*snapshot.collection.observations, extra),
        )
    )
    doc = document(updated, DocumentKind.DEPLOYMENT_RUNBOOK)
    for phase in (Phase.PREREQUISITES, Phase.VERIFICATION, Phase.RECOVERY):
        section = next(section for section in doc.sections if section.id == phase)
        assert any(
            row.state == "gap" and "fixture/other" in row.value for row in section.rows
        )
