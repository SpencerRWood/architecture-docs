"""Provider classification, explicit scopes and caller propagation without values."""

from dataclasses import replace

import pytest

from architecture_docs.collectors.content import configuration, executable
from architecture_docs.collectors.contracts import SourceFile
from architecture_docs.declarations import canonical
from architecture_docs.infisical_scope import Binding, Scope
from architecture_docs.model import Authority, CollectionResult, Observation, Provenance
from architecture_docs.reconciliation import reconcile
from architecture_docs.renderers.secrets import render
from architecture_docs.secret_mappings import mappings
from test_infisical import contract, location, reference

PATH = ".github/workflows/build.yml"


def workflow(
    repository: str, content: str, path: str = PATH
) -> tuple[Observation, ...]:
    provenance = Provenance(repository, path, "a" * 40, "b" * 40)
    return tuple(
        Observation("executable", Authority.EXECUTABLE, key, value, provenance)
        for key, value in executable(SourceFile(provenance, content))
    )


def declaration(
    repository: str, path: str, service: str = ""
) -> tuple[Observation, ...]:
    provenance = Provenance(repository, "architecture.toml", "a" * 40)
    table = "[[secret_locations]]" if service else "[secrets]"
    content = (
        f'version=1\n{table}\nproject="Infrastructure Dev"\n'
        f'environment="dev"\npath="{path}"\n'
        + (f'service="{service}"\n' if service else "")
    )
    return tuple(
        Observation("configuration", Authority.CONFIGURATION, key, value, provenance)
        for key, value in configuration(SourceFile(provenance, content))
    )


def test_explicit_named_project_path_wins_over_exact_repo_folder() -> None:
    other = Scope("project-id", "dev", "/actual-runtime")
    same = Scope("project-id", "dev", "/api")
    snapshot = reconcile(
        CollectionResult(
            (
                reference(),
                *declaration("fixture/api", other.path),
                location(other, object_id="runtime"),
                location(same, object_id="other"),
            )
        )
    )[0]
    entry = mappings(snapshot)[0][0]
    assert entry.status == "mapped"
    assert entry.locations[0][0].path == "/actual-runtime"
    assert entry.target[:3] == ("Infrastructure Dev", "dev", "/actual-runtime")
    assert entry.provider == "infisical"
    assert "secret.location" in {e.observation.key for e in entry.evidence}


def test_exact_name_fallback_is_unique_and_never_fuzzy() -> None:
    exact = Scope("project-id", "dev", "/api")
    different = Scope("project-id", "dev", "/api-service")
    for scope, status in ((exact, "mapped"), (different, "unresolved")):
        snapshot = reconcile(CollectionResult((reference(), location(scope))))[0]
        assert mappings(snapshot)[0][0].status == status
    snapshot = reconcile(
        CollectionResult(
            (
                reference(),
                location(exact),
                location(Scope("project-id", "prod", "/api"), object_id="prod"),
            )
        )
    )[0]
    assert mappings(snapshot)[0][0].status == "unresolved"


def test_two_similarly_named_consumers_keep_distinct_locations() -> None:
    facts: list[Observation] = []
    for name in ("synthetic-website-data", "synthetic-website-dbt"):
        repository = "fixture/" + name
        facts.extend(
            (
                replace(
                    reference(),
                    provenance=Provenance(repository, "architecture.toml", "a" * 40),
                ),
                *declaration(repository, "/" + name),
                location(Scope("project-id", "dev", "/" + name), object_id=name),
            )
        )
    snapshot = reconcile(CollectionResult(tuple(facts)))[0]
    assert {(m.repository, m.locations[0][0].path) for m in mappings(snapshot)[0]} == {
        ("fixture/synthetic-website-data", "/synthetic-website-data"),
        ("fixture/synthetic-website-dbt", "/synthetic-website-dbt"),
    }
    assert all(m.status == "mapped" for m in mappings(snapshot)[0])
    assert (
        render(snapshot).to_json()
        == render(reconcile(CollectionResult(tuple(reversed(facts))))[0]).to_json()
    )


def test_service_scope_overrides_repository_default_without_leaking_to_peers() -> None:
    from architecture_docs.declarations import EdgeKind, NodeKind  # noqa: PLC0415
    from test_reconciliation import observation  # noqa: PLC0415

    edges = tuple(
        observation(
            "architecture.edge",
            canonical(
                {
                    "kind": EdgeKind.CONSUMES_SECRET,
                    "source": {"kind": NodeKind.SERVICE, "name": name},
                    "target": {"kind": NodeKind.SECRET_REFERENCE, "name": "TOKEN"},
                }
            ),
            repository="fixture/api",
        )
        for name in ("worker", "web")
    )
    snapshot = reconcile(
        CollectionResult(
            (
                reference(),
                *edges,
                *declaration("fixture/api", "/default"),
                *declaration("fixture/api", "/worker", "worker"),
                location(Scope("project-id", "dev", "/default"), object_id="default"),
                location(Scope("project-id", "dev", "/worker"), object_id="worker"),
            )
        )
    )[0]
    assert {(m.consumer, m.locations[0][0].path) for m in mappings(snapshot)[0]} == {
        ("web", "/default"),
        ("worker", "/worker"),
    }


def test_github_provided_and_repository_secrets_do_not_lookup_infisical() -> None:
    facts = workflow(
        "fixture/api",
        """jobs:
  build:
    steps:
      - env:
          TOKEN: ${{ secrets.GITHUB_TOKEN }}
          DATABASE: ${{ secrets.DATABASE_URL }}
        run: echo fixture-sensitive-literal
""",
    )
    snapshot = reconcile(
        CollectionResult(
            (
                *facts,
                contract(
                    (
                        Binding(
                            "fixture/api",
                            "project-id",
                            "dev",
                            "/api",
                            injection="runtime",
                        ),
                    )
                ),
                location(key="DATABASE_URL"),
            )
        )
    )[0]
    entries = mappings(snapshot)[0]
    assert {(m.consumer_name, m.provider, m.status) for m in entries} == {
        ("GITHUB_TOKEN", "github-provided", "resolved"),
        ("DATABASE_URL", "github-repository-or-organization", "resolved"),
    }
    assert all(not m.locations for m in entries)
    assert "fixture-sensitive-literal" not in snapshot.to_json()
    assert (
        "GitHub-provided secrets and reusable-workflow forwarding"
        in render(snapshot).to_markdown()
    )


@pytest.mark.parametrize("inherit", [True, False])
def test_reusable_workflow_secret_propagates_to_each_caller(inherit: bool) -> None:
    callee = workflow(
        "fixture/workflows",
        """on: workflow_call
jobs:
  run:
    steps:
      - env:
          HOST: ${{ secrets.dbt_host }}
""",
    )
    callers: list[Observation] = []
    for repository in (
        "fixture/synthetic-website-data",
        "fixture/synthetic-website-dbt",
    ):
        passed = "inherit" if inherit else "\n      dbt_host: ${{ secrets.DBT_HOST }}"
        callers.extend(
            workflow(
                repository,
                "jobs:\n  build:\n"
                "    uses: fixture/workflows/.github/workflows/build.yml@v1\n"
                "    secrets: " + passed + "\n",
            )
        )
    snapshot = reconcile(CollectionResult((*callee, *callers)))[0]
    entries = mappings(snapshot)[0]
    assert len(entries) == 2
    assert {m.repository for m in entries} == {
        "fixture/synthetic-website-data",
        "fixture/synthetic-website-dbt",
    }
    assert all(
        m.consumer_name == ("dbt_host" if inherit else "DBT_HOST")
        and m.status == "resolved"
        and "fixture/workflows" in m.via
        for m in entries
    )
    assert all(
        any(e.observation.key == "workflow.call" for e in m.evidence) for m in entries
    )


def test_nested_workflow_and_secret_passed_as_input_follow_root_consumer() -> None:
    root = workflow(
        "fixture/api",
        """jobs:
  call:
    uses: fixture/middle/.github/workflows/build.yml@v1
    secrets:
      pass: ${{ secrets.DB_PASSWORD }}
""",
    )
    middle = workflow(
        "fixture/middle",
        """on: workflow_call
jobs:
  call:
    uses: fixture/workflows/.github/workflows/build.yml@v1
    with:
      dbt_password: ${{ secrets.pass }}
      ordinary: public-literal
""",
    )
    callee = workflow(
        "fixture/workflows",
        """on: workflow_call
jobs:
  run:
    steps:
      - env:
          PASSWORD: ${{ inputs.dbt_password }}
          ORDINARY: ${{ inputs.ordinary }}
""",
    )
    snapshot = reconcile(CollectionResult((*root, *middle, *callee)))[0]
    entries = mappings(snapshot)[0]
    assert len(entries) == 1
    assert entries[0].repository == "fixture/api"
    assert entries[0].consumer_name == "DB_PASSWORD"
    assert entries[0].status == "resolved"
    assert "fixture/workflows" in entries[0].via
    assert "public-literal" not in snapshot.to_json()
    assert all(m.consumer_name != "ordinary" for m in entries)


def test_true_unknowns_and_unbound_reusable_interfaces_stay_unresolved() -> None:
    callee = workflow(
        "fixture/workflows",
        """on: workflow_call
jobs:
  run:
    steps:
      - env:
          TOKEN: ${{ secrets.infrastructure_token }}
""",
    )
    snapshot = reconcile(CollectionResult((*callee, reference())))[0]
    entries = mappings(snapshot)[0]
    assert all(m.status == "unresolved" for m in entries)
    assert {m.provider for m in entries} == {"unknown", "reusable-workflow-input"}


def test_declared_location_without_matching_key_is_not_unknown_provider() -> None:
    scope = Scope("project-id", "dev", "/api")
    snapshot = reconcile(
        CollectionResult(
            (
                reference(),
                *declaration("fixture/api", "/api"),
                contract((), (scope,)),
                location(key="OTHER_KEY"),
            )
        )
    )[0]
    entry = mappings(snapshot)[0][0]
    assert entry.status == "missing-key"
    assert entry.provider == "infisical"
    assert entry.target[-2:] == ("/api", "TOKEN")


def test_unbound_optional_parameters_are_interfaces_not_unknown_locations() -> None:
    facts = workflow(
        "fixture/workflows",
        """on:
  workflow_call:
    secrets:
      dbt_password:
        required: false
      required_token:
        required: true
jobs:
  run:
    steps:
      - env:
          PASSWORD: ${{ secrets.dbt_password }}
          TOKEN: ${{ secrets.required_token }}
""",
    )
    snapshot = reconcile(CollectionResult(facts))[0]
    entries = {m.consumer_name: m for m in mappings(snapshot)[0]}
    assert entries["dbt_password"].status == "not-forwarded"
    assert entries["required_token"].status == "unresolved"
    assert all(m.provider == "reusable-workflow-input" for m in entries.values())
    assert any(
        e.observation.key == "workflow.secret_parameter"
        for e in entries["dbt_password"].evidence
    )
    text = render(snapshot).to_markdown()
    assert "Optional reusable-workflow parameters without caller bindings" in text


def test_caller_cycles_and_wrong_pins_do_not_invent_origins() -> None:
    callee = workflow(
        "fixture/workflows",
        """on: workflow_call
jobs:
  call:
    uses: fixture/workflows/.github/workflows/build.yml@v1
    secrets: inherit
  run:
    steps:
      - env:
          TOKEN: ${{ secrets.TOKEN }}
""",
    )
    snapshot = reconcile(CollectionResult(callee))[0]
    assert all(m.status == "unresolved" for m in mappings(snapshot)[0])
    root = workflow(
        "fixture/api",
        "jobs:\n  call:\n    uses: fixture/workflows/.github/workflows/build.yml@"
        + "c" * 40
        + "\n    secrets: inherit\n",
    )
    pinned = reconcile(CollectionResult((*callee, *root)))[0]
    assert all(m.repository != "fixture/api" for m in mappings(pinned)[0])


def test_workflow_refresh_uses_approved_pinned_blobs_and_preserves_failures() -> None:
    from architecture_docs.collection import collect  # noqa: PLC0415
    from architecture_docs.workflow_refresh import refresh_workflows  # noqa: PLC0415
    from test_collection import FixtureGitHub, registry  # noqa: PLC0415

    fixture = FixtureGitHub()
    fixture.files[PATH] = (
        b"jobs:\n  call:\n"
        b"    uses: fixture/workflows/.github/workflows/build.yml@v1\n"
        b"    secrets: inherit\n"
    )
    initial = collect(registry(), fixture.client())
    retained = replace(
        initial,
        observations=tuple(
            o
            for o in initial.observations
            if o.key
            not in {
                "workflow.call",
                "workflow.secret_ref",
                "workflow.reusable",
                "workflow.input_ref",
                "workflow.secret_parameter",
            }
        ),
    )
    current = refresh_workflows(retained, registry(), fixture.client())
    assert current.complete
    assert any(o.key == "workflow.call" for o in current.observations)
    assert all(request.method == "GET" for request in fixture.requests)
    fixture.fail_path = "/git/blobs/"
    failed = refresh_workflows(retained, registry(), fixture.client())
    assert not failed.complete
    snapshot = reconcile(failed, reconcile(retained)[0])[0]
    assert snapshot.publication_blocked


@pytest.mark.parametrize(
    "extra",
    [
        'secret_value="excluded"',
        'path="../bad"',
        'project="https://user:password@host"',
    ],
)
def test_location_contract_rejects_values_or_invalid_scope(extra: str) -> None:
    source = SourceFile(
        Provenance("fixture/api", "architecture.toml", "a" * 40),
        'version=1\n[secrets]\nproject="Infrastructure Dev"\n'
        'environment="dev"\npath="/api"\n' + extra,
    )
    with pytest.raises((ValueError, TypeError)):
        configuration(source)
