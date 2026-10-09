"""Estate completeness is independent of source success and old graph topology."""

import json
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from architecture_docs.codec import collection_from_json, snapshot_from_json
from architecture_docs.collection import collect
from architecture_docs.config import Registry, Repository, load_registry
from architecture_docs.declarations import NodeKind
from architecture_docs.estate import (
    EstateContract,
    ExpectedRepository,
    RequiredDomain,
    contract_from_data,
    evaluate,
)
from architecture_docs.model import (
    Authority,
    CollectionResult,
    Failure,
    RepositoryInventory,
    SourceCoverage,
)
from architecture_docs.reconciliation import Policy, Tombstone, reconcile
from architecture_docs.renderers import render_documents
from architecture_docs.store import SnapshotStore
from estate_fixtures import FIXTURE_ESTATE
from test_collection import FixtureGitHub, registry
from test_reconciliation import node, observation

DOMAIN_KINDS = {
    "deployment_release": NodeKind.RELEASE_CONTRACT,
    "runtime_infrastructure": NodeKind.SERVICE,
    "data_storage": NodeKind.STORAGE,
    "automation_orchestration": NodeKind.ORCHESTRATION,
    "secrets": NodeKind.SECRET_REFERENCE,
    "runbooks": NodeKind.PROCEDURE,
}


def full_estate() -> CollectionResult:
    """Three explicit repositories jointly supply the six required domains."""
    contract = EstateContract(
        (
            ExpectedRepository("fixture/core", "core", True),
            ExpectedRepository("fixture/service", "service", True),
            ExpectedRepository("fixture/template", "supporting", False),
        ),
        tuple(RequiredDomain(name, (kind,)) for name, kind in DOMAIN_KINDS.items()),
    )
    facts = tuple(
        observation(
            "architecture.node",
            node(name, kind),
            repository="fixture/core" if index < 3 else "fixture/service",
        )
        for index, (name, kind) in enumerate(DOMAIN_KINDS.items())
    )
    return CollectionResult(
        (*facts, contract.observation()),
        coverage=tuple(
            sorted(
                {
                    SourceCoverage(
                        fact.provenance.repository,
                        fact.provenance.source,
                        fact.collector,
                        fact.provenance.revision,
                    )
                    for fact in facts
                }
            )
        ),
    )


def test_full_estate_has_six_domains_and_visible_optional_omissions() -> None:
    result = full_estate()
    coverage = evaluate(result)
    assert result.complete
    assert coverage.complete
    assert len(coverage.domains) == 6
    optional = coverage.repositories[-1]
    assert not optional.required
    assert not optional.represented
    snapshot, _ = reconcile(result)
    assert snapshot.estate_state == "complete_with_gaps"  # Secret fields are absent.
    assert not snapshot.publication_blocked
    for doc in render_documents(snapshot).documents:
        assert "fixture/template" in doc.to_markdown()
        assert "optional" in doc.to_markdown()


@pytest.mark.parametrize("missing", list(DOMAIN_KINDS))
def test_every_missing_domain_blocks_successful_collection(missing: str) -> None:
    result = full_estate()
    result = replace(
        result,
        observations=tuple(
            fact
            for fact in result.observations
            if fact.key != "architecture.node"
            or json.loads(fact.value)["name"] != missing
        ),
    )
    assert result.complete
    snapshot, _ = reconcile(result)
    assert snapshot.estate_state == "incomplete_estate"
    assert snapshot.publication_blocked
    assert not next(
        item for item in evaluate(result).domains if item.name == missing
    ).repositories


@pytest.mark.parametrize(
    "state", ["missing", "skipped", "failed", "uncovered", "old_revision"]
)
def test_required_repository_needs_current_successful_evidence(state: str) -> None:
    result = full_estate()
    service = "fixture/service"
    first, _ = reconcile(result)
    if state in {"missing", "skipped"}:
        result = replace(
            result,
            observations=tuple(
                fact
                for fact in result.observations
                if fact.provenance.repository != service
            ),
            skipped=(service,) if state == "skipped" else (),
            coverage=tuple(
                scope
                for scope in result.coverage
                if state != "skipped" or scope.repository != service
            ),
        )
    elif state == "failed":
        fact = next(
            fact
            for fact in result.observations
            if fact.provenance.repository == service
        )
        result = replace(
            result, failures=(Failure("fixture", fact.provenance, "parse_error"),)
        )
    else:
        result = replace(
            result,
            coverage=tuple(
                replace(scope, revision="c" * 40)
                if scope.repository == service and state == "old_revision"
                else scope
                for scope in result.coverage
                if state != "uncovered" or scope.repository != service
            ),
        )
    snapshot, diff = reconcile(result, first)
    assert snapshot.estate_state == "incomplete_estate"
    assert not next(
        item for item in snapshot.estate_coverage.repositories if item.name == service
    ).represented
    assert any(change.category == "estate_coverage" for change in diff.changes)


def test_tiny_success_is_incomplete_and_optional_cannot_replace_required() -> None:
    result = full_estate()
    result = replace(
        result,
        observations=tuple(
            replace(
                fact, provenance=replace(fact.provenance, repository="fixture/template")
            )
            if fact.collector != "estate"
            else fact
            for fact in result.observations
        ),
        coverage=tuple(
            replace(scope, repository="fixture/template") for scope in result.coverage
        ),
    )
    assert result.complete
    assert not evaluate(result).complete
    assert all(domain.repositories for domain in evaluate(result).domains)
    assert evaluate(CollectionResult()).configured is False


def test_config_drift_is_material_and_does_not_retain_old_expectations() -> None:
    result = full_estate()
    first, _ = reconcile(result)
    contract = contract_from_data(json.loads(result.observations[-1].value))
    changed = replace(
        contract,
        repositories=(
            *contract.repositories,
            ExpectedRepository("fixture/new", "core", True),
        ),
    )
    result = replace(
        result, observations=(*result.observations[:-1], changed.observation())
    )
    second, diff = reconcile(result, first)
    assert second.estate_state == "incomplete_estate"
    assert any(change.category == "estate_coverage" for change in diff.changes)
    assert (
        sum(item.observation.key == "estate.contract" for item in second.evidence) == 1
    )
    assert snapshot_from_json(second.to_json()) == second
    assert collection_from_json(result.to_json()) == result


def test_predicate_change_is_material_even_when_coverage_is_the_same() -> None:
    result = full_estate()
    first, _ = reconcile(result)
    contract = contract_from_data(json.loads(result.observations[-1].value))
    changed = replace(
        contract,
        domains=tuple(
            replace(domain, fact_prefixes=("package.name",))
            for domain in contract.domains
        ),
    )
    result = replace(
        result, observations=(*result.observations[:-1], changed.observation())
    )
    second, diff = reconcile(result, first)
    assert second.estate_coverage.repositories == first.estate_coverage.repositories
    assert second.estate_coverage.domains == first.estate_coverage.domains
    assert second.estate_coverage.contract_id != first.estate_coverage.contract_id
    assert {change.category for change in diff.changes} == {"estate_coverage"}


def test_tombstoned_evidence_cannot_satisfy_required_estate() -> None:
    result = full_estate()
    assert evaluate(result).complete
    snapshot, _ = reconcile(
        result, policy=Policy(tombstones=(Tombstone("fixture/core"),))
    )
    assert snapshot.estate_state == "incomplete_estate"
    assert not next(
        item
        for item in snapshot.estate_coverage.repositories
        if item.name == "fixture/core"
    ).represented


def test_coverage_survives_database_restart_and_history(database_url: str) -> None:
    store = SnapshotStore(database_url)
    first = store.reconcile(full_estate())
    second = store.reconcile(
        CollectionResult(observations=(full_estate().observations[-1],))
    )
    restarted = SnapshotStore(database_url)
    assert restarted.get(first.snapshot.id).estate_coverage.complete
    assert restarted.latest() == second.snapshot
    assert second.snapshot.estate_state == "incomplete_estate"
    assert any(change.category == "estate_coverage" for change in second.diff.changes)


def test_classification_changes_expectations_without_changing_authority() -> None:
    fact = observation(
        "source.kind", "documentation", "README.md", Authority.DOCUMENTATION
    )
    contract = replace(
        FIXTURE_ESTATE,
        domains=(RequiredDomain("documentation", fact_prefixes=("source.kind",)),),
    )
    result = CollectionResult(
        (fact, contract.observation()),
        coverage=(SourceCoverage("fixture/service", "README.md", "fixture", "a" * 40),),
    )
    assert not evaluate(result).complete
    supporting = replace(
        contract,
        repositories=(ExpectedRepository("fixture/service", "supporting", True),),
    )
    result = replace(result, observations=(fact, supporting.observation()))
    assert evaluate(result).complete
    snapshot, _ = reconcile(result)
    assert all(
        item.observation.authority == Authority.DOCUMENTATION
        for item in snapshot.evidence
        if item.observation.collector != "estate"
    )


def test_registry_accounts_for_supported_estate_with_bounded_approvals() -> None:
    loaded = load_registry(Path("config/repositories.toml"))
    assert loaded.estate is not None
    assert len(loaded.repositories) == len(loaded.estate.repositories) == 29
    assert len(loaded.estate.domains) == 6
    names = {repo.name for repo in loaded.repositories}
    assert "SpencerRWood/wood-data-platform" not in names
    assert "SpencerRWood/homelab" not in names
    assert "SpencerRWood/homelab" not in {
        repo.name for repo in loaded.estate.repositories
    }
    assert "SpencerRWood/website-marketing-simulation" not in names
    assert {
        "SpencerRWood/pi-config",
        "SpencerRWood/recovery-verification",
        "SpencerRWood/synthetic-website-analytics-platform",
    } <= names
    assert "SpencerRWood/wood-agents" not in names
    assert "SpencerRWood/sql-control-cli" not in names
    assert all("*" not in path for repo in loaded.repositories for path in repo.paths)
    assert not Repository("o/r", ("docs/*.md",)).approves("docs/private/README.md")
    with pytest.raises(ValueError, match="paths"):
        Repository("o/r", ("docs/**",))


def test_collect_persists_expectations_even_when_archived_or_failed() -> None:
    fixture = FixtureGitHub()
    fixture.archived = True
    result = collect(registry(), fixture.client())
    assert len(result.observations) == 3
    assert result.observations[0].key == "estate.contract"
    assert any(
        item.key == "repository.archived" and item.value == "true"
        for item in result.observations
    )
    assert not evaluate(result).complete
    fixture.archived = False
    fixture.fail_path = "/repos/"
    result = collect(registry(), fixture.client())
    assert evaluate(result).repositories[0].reason == "source_failed"
    assert "fixture-sensitive-literal" not in result.to_json()


@pytest.mark.parametrize(
    "mutation",
    [
        "empty_repos",
        "empty_domains",
        "duplicate_repo",
        "duplicate_domain",
        "unknown",
        "unknown_kind",
        "string_kinds",
        "string_domains",
        "bad_name",
        "core_optional",
        "invalid_required",
        "duplicate_prefix",
        "list_prefix",
        "missing_classifier",
    ],
)
def test_malformed_contracts_are_rejected_without_disclosing_input(  # noqa: PLR0912 -- explicit adversarial shapes
    mutation: str,
) -> None:
    data = json.loads(json.dumps(asdict(FIXTURE_ESTATE)))
    if mutation == "empty_repos":
        data["repositories"] = []
    elif mutation == "empty_domains":
        data["domains"] = []
    elif mutation == "duplicate_repo":
        data["repositories"] *= 2
    elif mutation == "duplicate_domain":
        data["domains"] *= 2
    elif mutation == "unknown":
        data["domains"][0]["fixture-sensitive-literal"] = True
    elif mutation == "unknown_kind":
        data["domains"][0]["node_kinds"] = ["unknown"]
    elif mutation == "string_kinds":
        data["domains"][0]["node_kinds"] = "service"
    elif mutation == "string_domains":
        data["domains"] = "fixture-sensitive-literal"
    elif mutation == "bad_name":
        data["repositories"][0]["name"] = "../repo"
    elif mutation == "core_optional":
        data["repositories"][0].update(classification="core", required=False)
    elif mutation == "invalid_required":
        data["repositories"][0]["required"] = 1
    elif mutation == "duplicate_prefix":
        data["domains"][0]["fact_prefixes"] *= 2
    elif mutation == "list_prefix":
        data["domains"][0]["fact_prefixes"] = [[]]
    else:
        del data["repositories"][0]["classification"]
    payload = replace(FIXTURE_ESTATE.observation(), value=json.dumps(data))
    with pytest.raises(ValueError, match="invalid collection record") as error:
        collection_from_json(CollectionResult((payload,)).to_json())
    assert "fixture-sensitive-literal" not in str(error.value)


def test_ambiguous_or_forged_contract_and_unexpected_approvals_fail_closed() -> None:
    contract = FIXTURE_ESTATE.observation()
    for observations in [
        (contract, contract),
        (replace(contract, collector="github"),),
        (replace(contract, authority=Authority.GITHUB),),
        (
            replace(
                contract, provenance=replace(contract.provenance, revision="a" * 40)
            ),
        ),
    ]:
        with pytest.raises(ValueError, match="ambiguous"):
            evaluate(CollectionResult(observations))
    with pytest.raises(ValueError, match="expectation"):
        Registry((Repository("fixture/other", ("README.md",)),), estate=FIXTURE_ESTATE)


@pytest.mark.parametrize(
    "contradiction", ["skipped", "source_revisions", "inventory_revisions"]
)
def test_contradictory_serialized_coverage_is_rejected(contradiction: str) -> None:
    result = full_estate()
    if contradiction == "skipped":
        result = replace(result, skipped=("fixture/core",))
    elif contradiction == "source_revisions":
        result = replace(
            result,
            coverage=(*result.coverage, replace(result.coverage[0], revision="c" * 40)),
        )
    else:
        inventory = RepositoryInventory(
            "fixture/core", "a" * 40, ("architecture.toml",), ("architecture.toml",)
        )
        result = replace(
            result, inventories=(inventory, replace(inventory, revision="c" * 40))
        )
    with pytest.raises(ValueError, match="invalid collection record"):
        collection_from_json(result.to_json())
