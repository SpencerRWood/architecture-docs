"""Offline security, approvals, mapping and outage regressions."""

import json
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

import httpx
import pytest

from architecture_docs.codec import collection_from_json, snapshot_from_json
from architecture_docs.collection import collect
from architecture_docs.collectors.github import SourceError
from architecture_docs.collectors.infisical import (
    COLLECTOR,
    NAMESPACE,
    Infisical,
    Location,
)
from architecture_docs.collectors.metadata_boundary import metadata_json
from architecture_docs.config import load_registry
from architecture_docs.database import connection
from architecture_docs.declarations import NodeKind, canonical
from architecture_docs.estate import EstateContract, ExpectedRepository, RequiredDomain
from architecture_docs.infisical_scope import (
    Binding,
    InfisicalConfig,
    Scope,
    load_infisical,
)
from architecture_docs.integration import main as integration_main
from architecture_docs.model import (
    Authority,
    CollectionResult,
    Failure,
    Observation,
    Provenance,
    RepositoryInventory,
    SourceCoverage,
)
from architecture_docs.publishing import publish
from architecture_docs.reconciliation import reconcile
from architecture_docs.renderers.secrets import render
from architecture_docs.secret_mappings import mappings
from architecture_docs.store import SnapshotStore
from test_collection import FixtureGitHub, registry
from test_estate import full_estate
from test_publication import DriveService

SCOPE = Scope("project-id", "dev", "/api")
CONFIG = InfisicalConfig("https://infisical.example", (SCOPE,))
SENTINEL = "fixture-sensitive-literal"


def secret(
    key: str = "TOKEN", scope: Scope = SCOPE, **extra: object
) -> dict[str, object]:
    return {
        "id": "metadata-id",
        "workspace": scope.project,
        "environment": scope.environment,
        "secretPath": scope.path,
        "secretKey": key,
        "version": 1,
        "secretValueHidden": True,
        "secretValue": "<hidden-by-infisical>",
        **extra,
    }


def client(
    config: InfisicalConfig = CONFIG,
    entries: list[dict[str, object]] | None = None,
    status: int = 200,
    requests: list[httpx.Request] | None = None,
) -> Infisical:
    def handle(request: httpx.Request) -> httpx.Response:
        if requests is not None:
            requests.append(request)
        if "/workspace/" in request.url.path:
            return httpx.Response(
                200,
                json={"workspace": {"id": "project-id", "name": "Infrastructure Dev"}},
            )
        return httpx.Response(
            status,
            json={
                "secrets": entries if entries is not None else [secret()],
                "imports": [],
            },
        )

    return Infisical(
        config, "fixture-access-token", transport=httpx.MockTransport(handle)
    )


def reference(name: str = "TOKEN", **attributes: str) -> Observation:
    return Observation(
        "configuration",
        Authority.CONFIGURATION,
        "architecture.node",
        canonical({"kind": "secret_reference", "name": name, "attributes": attributes}),
        Provenance("fixture/api", "architecture.toml", "a" * 40),
    )


def location(
    scope: Scope = SCOPE, key: str = "TOKEN", object_id: str = "metadata-id"
) -> Observation:
    loc = Location(
        scope.project,
        "Infrastructure Dev",
        scope.environment,
        scope.path,
        key,
        object_id,
        1,
    )
    return Observation(
        COLLECTOR,
        Authority.CONFIGURATION,
        "infisical.location",
        canonical(asdict(loc)),
        Provenance(NAMESPACE, scope.source, "metadata-sha256:fixture"),
    )


def contract(
    bindings: tuple[Binding, ...], scopes: tuple[Scope, ...] = (SCOPE,)
) -> Observation:
    return Observation(
        COLLECTOR,
        Authority.CONFIGURATION,
        "infisical.contract",
        replace(CONFIG, scopes=scopes, bindings=bindings).contract,
        Provenance(NAMESPACE, "infisical:approval", None),
    )


def test_fixed_read_only_metadata_transport_and_serialization() -> None:
    requests: list[httpx.Request] = []
    source = client(requests=requests)
    result = source.collect()
    source.close()
    assert result.complete
    assert len(result.observations) == 2
    assert all(req.method == "GET" for req in requests)
    query = requests[-1].url.params
    assert query["viewSecretValue"] == "false"
    for name in (
        "expandSecretReferences",
        "recursive",
        "includeImports",
        "includePersonalOverrides",
    ):
        assert query[name] == "false"
    assert (query["projectId"], query["environment"], query["secretPath"]) == (
        "project-id",
        "dev",
        "/api",
    )
    assert "secretValue" not in result.to_json()
    assert "fixture-access-token" not in result.to_json()
    assert collection_from_json(result.to_json()) == result
    snapshot = reconcile(result)[0]
    assert snapshot_from_json(snapshot.to_json()).to_json() == snapshot.to_json()
    assert "github.com" not in render(snapshot).to_markdown()
    assert "metadata-sha256:" in render(snapshot).to_json()
    assert "metadata-sha256:" not in render(snapshot).to_markdown()
    assert (
        "Infisical: Infrastructure Dev / dev / /api / TOKEN"
        in render(snapshot).to_markdown()
    )


@pytest.mark.parametrize(
    "field",
    [
        "secretValue",
        "value",
        "secretValueCiphertext",
        "encryptedValue",
        "password",
        "credential",
        "token",
        "secret\\u0056alue",
    ],
)
def test_value_tokens_are_rejected_before_scalar_deserialization(
    field: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    decoded: list[bytes | str] = []
    original = json.loads

    def tracked(value: Any, *args: Any, **kwargs: Any) -> Any:
        decoded.append(value)
        return original(value, *args, **kwargs)

    monkeypatch.setattr(json, "loads", tracked)
    body = ('{"secrets":[{"' + field + '":"' + SENTINEL + '"}]}').encode()
    with pytest.raises(SourceError, match="unexpected_value_field"):
        metadata_json(body)
    assert all(SENTINEL not in str(value) for value in decoded)


def test_unexpected_value_never_reaches_snapshots_graph_logs_or_documents(
    caplog: pytest.LogCaptureFixture,
) -> None:
    result = client(entries=[secret(secretValue=SENTINEL)]).collect()
    assert [f.reason for f in result.failures] == ["unexpected_value_field"]
    assert not any(o.key == "infisical.location" for o in result.observations)
    snapshot = reconcile(result)[0]
    assert (
        SENTINEL
        not in result.to_json()
        + snapshot.to_json()
        + render(snapshot).to_json()
        + render(snapshot).to_markdown()
        + caplog.text
    )
    assert not snapshot.graph.nodes
    assert snapshot.publication_blocked


def test_unused_fields_are_discarded_without_deserializing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = json.loads

    def tracked(value: Any, *args: Any, **kwargs: Any) -> Any:
        assert SENTINEL not in str(value)
        return original(value, *args, **kwargs)

    monkeypatch.setattr(json, "loads", tracked)
    data = metadata_json(
        json.dumps(
            {"secrets": [secret(secretComment=SENTINEL, secretReminderNote=SENTINEL)]}
        ).encode()
    )
    assert SENTINEL not in str(data)


@pytest.mark.parametrize(
    "body",
    [
        b'{"id":1,"id":2}',
        b'{"secrets":[1,]}',
        b'{"id":}',
        b"{}false",
        b'{"id":1,}',
        b'{"id":"\\x"}',
        b"[" * 22 + b"]" * 22,
    ],
)
def test_malformed_json_fails_closed(body: bytes) -> None:
    with pytest.raises(SourceError):
        metadata_json(body)


def test_approved_scope_is_enforced_before_any_request() -> None:
    requests: list[httpx.Request] = []
    source = client(requests=requests)
    for scope in (
        Scope("other", "dev", "/api"),
        Scope("project-id", "prod", "/api"),
        Scope("project-id", "dev", "/api/child"),
    ):
        with pytest.raises(SourceError, match="unapproved_metadata_scope"):
            source.locations(scope)
    assert not requests
    with pytest.raises(ValueError, match="outside approved scope"):
        replace(
            CONFIG, bindings=(Binding("fixture/api", "project-id", "prod", "/api"),)
        )


@pytest.mark.parametrize(
    "override",
    [
        {"environment": "prod"},
        {"workspace": "other"},
        {"secretPath": "/api/child"},
        {"secretValueHidden": False},
        {"secretKey": "bad key"},
        {"version": 0},
    ],
)
def test_response_scope_mask_and_identifier_checks(override: dict[str, object]) -> None:
    result = client(entries=[secret(**override)]).collect()  # type: ignore[arg-type]
    assert not result.complete
    assert not any(o.key == "infisical.location" for o in result.observations)


def test_response_and_inventory_bounds_and_credentials() -> None:
    assert (
        client(replace(CONFIG, max_secrets=1), [secret(), secret(id="second")])
        .collect()
        .failures[0]
        .reason
        == "metadata_secret_limit"
    )
    assert (
        client(replace(CONFIG, max_response_bytes=1)).collect().failures[0].reason
        == "metadata_response_limit"
    )
    assert (
        Infisical(CONFIG).collect().failures[0].reason == "metadata_credentials_missing"
    )
    assert (
        client(entries=[secret(), secret()]).collect().failures[0].reason
        == "duplicate_metadata_object"
    )
    assert client(status=403).collect().failures[0].reason == "access_denied"


def test_exact_declaration_and_explicit_alias_precedence() -> None:
    binding = Binding(
        "fixture/api",
        "project-id",
        "dev",
        "/api",
        "DATABASE_URL",
        "ACTUAL_DATABASE_URL",
    )
    observations = (
        reference("DATABASE_URL", project="wrong", environment="dev", path="/other"),
        location(key="ACTUAL_DATABASE_URL"),
        contract((binding,)),
    )
    snapshot = reconcile(CollectionResult(observations))[0]
    mapped, unused = mappings(snapshot)
    assert mapped[0].status == "mapped"
    assert mapped[0].locations[0][0].key == "ACTUAL_DATABASE_URL"
    assert not unused
    exact = reconcile(
        CollectionResult(
            (
                reference(project="project-id", environment="dev", path="/api"),
                location(),
            )
        )
    )[0]
    assert mappings(exact)[0][0].status == "mapped"
    alias = reconcile(
        CollectionResult(
            (
                reference(
                    "DATABASE_URL",
                    project="project-id",
                    environment="dev",
                    path="/api",
                    infisical_key="ACTUAL_DATABASE_URL",
                ),
                location(key="ACTUAL_DATABASE_URL"),
            )
        )
    )[0]
    assert mappings(alias)[0][0].status == "mapped"


def test_mapping_retains_declared_owner_relationship() -> None:
    ownership = Observation(
        "configuration",
        Authority.CONFIGURATION,
        "architecture.edge",
        canonical(
            {
                "kind": "owned_by",
                "source": {"kind": "secret_reference", "name": "TOKEN"},
                "target": {"kind": "system", "name": "owning-system"},
            }
        ),
        Provenance("fixture/api", "architecture.toml", "a" * 40),
    )
    snapshot = reconcile(
        CollectionResult(
            (
                reference(
                    project=SCOPE.project,
                    environment=SCOPE.environment,
                    path=SCOPE.path,
                ),
                ownership,
                location(),
            )
        )
    )[0]
    assert mappings(snapshot)[0][0].owner == "owning-system"


def test_same_name_multiple_paths_and_environments_are_ambiguous() -> None:
    scopes = (
        SCOPE,
        Scope("project-id", "dev", "/worker"),
        Scope("project-id", "prod", "/api"),
    )
    bindings = tuple(
        Binding("fixture/api", s.project, s.environment, s.path) for s in scopes
    )
    facts = (
        reference(),
        contract(bindings, scopes),
        *(location(s, object_id=f"id-{i}") for i, s in enumerate(scopes)),
    )
    snapshot = reconcile(CollectionResult(facts))[0]
    mapped, unused = mappings(snapshot)
    assert mapped[0].status == "ambiguous"
    assert len(mapped[0].locations) == 3
    assert not unused
    text = render(snapshot).to_markdown()
    assert "Ambiguous mappings" in text
    assert "/worker" in text
    assert "prod" in text
    assert (
        reconcile(CollectionResult(tuple(reversed(facts))))[0].to_json()
        == snapshot.to_json()
    )
    assert (
        render(snapshot_from_json(snapshot.to_json())).to_json()
        == render(snapshot).to_json()
    )


def test_no_unassociated_global_name_match_and_unused_inventory() -> None:
    snapshot = reconcile(
        CollectionResult(
            (reference(), location(Scope("project-id", "dev", "/other-service")))
        )
    )[0]
    mapped, unused = mappings(snapshot)
    assert mapped[0].status == "unresolved"
    assert len(unused) == 1
    text = render(snapshot).to_markdown()
    assert "Unresolved repository secret references" in text
    assert "Approved Infisical metadata with no known consumer" in text


@pytest.mark.parametrize("change", ["new_location", "metadata_version", "association"])
def test_secret_metadata_changes_publish_manifest_in_place(
    database_url: str, change: str
) -> None:
    estate = full_estate()
    association = contract(
        (Binding("fixture/api", SCOPE.project, SCOPE.environment, SCOPE.path),)
    )
    initial_location = location()
    changed_location = replace(
        initial_location,
        value=canonical({**json.loads(initial_location.value), "version": 2}),
    )

    def collection(facts: tuple[Observation, ...]) -> CollectionResult:
        observations = (*estate.observations, reference(), *facts)
        return CollectionResult(
            observations,
            coverage=tuple(
                sorted(
                    {
                        SourceCoverage(
                            o.provenance.repository,
                            o.provenance.source,
                            o.collector,
                            o.provenance.revision,
                        )
                        for o in observations
                    }
                )
            ),
        )

    before_facts = (
        (association,)
        if change == "new_location"
        else (initial_location, contract(()))
        if change == "association"
        else (association, initial_location)
    )
    before, _ = reconcile(collection(before_facts))
    after, diff = reconcile(
        collection(
            (
                association,
                changed_location if change == "metadata_version" else initial_location,
            )
        ),
        before,
    )
    assert not after.publication_blocked
    assert diff.material
    assert "secret_topology" in {c.category for c in diff.changes}
    assert render(before).content_hash != render(after).content_hash
    assert mappings(after)[0][0].status == "mapped"
    if change == "metadata_version":
        assert before.graph == after.graph
    service = DriveService()
    first = publish(before, service.client(), database_url, "parent")
    original_id = next(
        a.file_id for a in first.artifacts if a.artifact == "secrets-manifest"
    )
    assert original_id is not None
    service.writes.clear()
    second = publish(
        snapshot_from_json(after.to_json()), service.client(), database_url, "parent"
    )
    manifest = next(a for a in second.artifacts if a.artifact == "secrets-manifest")
    assert second.complete
    assert manifest.state == "published"
    assert manifest.file_id == original_id
    assert service.texts[original_id] == render(after).to_markdown()
    service.writes.clear()
    assert publish(after, service.client(), database_url, "parent").complete
    assert not service.writes


def test_incomplete_or_conflicting_alias_declarations_do_not_fall_back_to_name() -> (
    None
):
    association = contract(
        (Binding("fixture/api", SCOPE.project, SCOPE.environment, SCOPE.path),)
    )
    partial = reconcile(
        CollectionResult((reference(path="/other"), association, location()))
    )[0]
    assert mappings(partial)[0][0].status == "unresolved"
    declaration = reference(
        project=SCOPE.project,
        environment=SCOPE.environment,
        path=SCOPE.path,
        infisical_key="ACTUAL_TOKEN",
    )
    conflicting = replace(
        declaration,
        authority=Authority.DOCUMENTATION,
        value=canonical(
            {
                "kind": "secret_reference",
                "name": "TOKEN",
                "attributes": {"infisical_key": "OLD_TOKEN"},
            }
        ),
    )
    snapshot = reconcile(
        CollectionResult((declaration, conflicting, association, location()))
    )[0]
    assert mappings(snapshot)[0][0].status == "unresolved"
    explicit = contract(
        (
            Binding(
                "fixture/api",
                SCOPE.project,
                SCOPE.environment,
                SCOPE.path,
                "TOKEN",
                "MISSING_ALIAS",
            ),
        )
    )
    missing = reconcile(CollectionResult((reference(), explicit, location())))[0]
    assert mappings(missing)[0][0].status == "missing-key"


def test_infisical_outage_retains_stale_locations_despite_github_inventory() -> None:
    first = client().collect()
    declared = reference(project="project-id", environment="dev", path="/api")
    snapshot = reconcile(replace(first, observations=(*first.observations, declared)))[
        0
    ]
    failed = client(status=503).collect()
    stale = reconcile(
        replace(
            failed,
            observations=(*failed.observations, declared),
            inventories=(RepositoryInventory(NAMESPACE, "a" * 40, (), ("*",)),),
        ),
        snapshot,
    )[0]
    assert mappings(stale)[0][0].status == "stale"
    assert stale.publication_blocked
    assert any(
        e.verification == "stale" and e.observation.key == "infisical.location"
        for e in stale.evidence
    )
    assert "stale" in render(stale).to_markdown()
    # GitHub outage does not make successfully verified Infisical metadata stale.
    github_failed = reconcile(
        replace(
            first,
            failures=(
                Failure(
                    "github_source",
                    Provenance(NAMESPACE, "github:repository", None),
                    "unavailable",
                ),
            ),
        ),
        snapshot,
    )[0]
    assert all(
        e.verification == "verified"
        for e in github_failed.evidence
        if e.observation.key == "infisical.location"
    )
    # A successful empty inventory is the separate authority that proves absence.
    empty = reconcile(client(entries=[]).collect(), snapshot)[0]
    assert not any(e.observation.key == "infisical.location" for e in empty.evidence)


def test_untrusted_persisted_metadata_rejects_value_fields() -> None:
    data = asdict(location())
    payload = json.loads(str(data["value"]))
    payload["secret_value"] = SENTINEL
    data["value"] = canonical(payload)
    with pytest.raises(ValueError, match="invalid collection record"):
        collection_from_json(
            canonical(
                {
                    "schema_version": 1,
                    "observations": [data],
                    "failures": [],
                    "skipped": [],
                }
            )
        )


@pytest.mark.parametrize(
    "url",
    [
        "http://example.org",
        "https://user:password@example.org",
        "https://example.org/api",
        "https://example.org?query=1",
    ],
)
def test_url_cannot_broaden_transport(url: str) -> None:
    with pytest.raises(ValueError, match="invalid Infisical URL"):
        replace(CONFIG, url=url)


def test_approval_loader_rejects_credentials_unknown_fields_and_consumers(
    tmp_path: Path,
) -> None:
    file = tmp_path / "infisical.toml"
    base = 'version=1\nurl="https://example.org"\n[[scopes]]\nproject="project-id"\nenvironment="dev"\npath="/api"\n'
    file.write_text(base)
    assert load_infisical(file, {"fixture/api"}).scopes == (SCOPE,)
    for text in (
        base.replace("version=1", "version=2"),
        base + 'token="bad"\n',
        base + '[[bindings]]\nrepository="fixture/excluded"\nproject="project-id"\n'
        'environment="dev"\npath="/api"\n',
    ):
        file.write_text(text)
        with pytest.raises(ValueError, match="invalid Infisical approval contract"):
            load_infisical(file, {"fixture/api"})


def test_collection_dispatches_independent_metadata_and_rejects_wrong_approvals() -> (
    None
):
    approved = replace(registry(), infisical=CONFIG)
    source = client()
    collected = collect(approved, FixtureGitHub().client(), infisical=source)
    assert collected.complete
    assert any(o.key == "infisical.location" for o in collected.observations)
    with pytest.raises(ValueError, match="approval mismatch"):
        collect(
            approved,
            FixtureGitHub().client(),
            infisical=client(replace(CONFIG, max_secrets=1)),
        )


def test_partial_scope_failure_keeps_only_failed_scope_stale() -> None:
    worker = Scope(SCOPE.project, "dev", "/worker")
    config = replace(CONFIG, scopes=(SCOPE, worker))

    def source(fail_worker: bool) -> Infisical:
        def handle(request: httpx.Request) -> httpx.Response:
            if "/workspace/" in request.url.path:
                return httpx.Response(
                    200,
                    json={
                        "workspace": {"id": SCOPE.project, "name": "Infrastructure Dev"}
                    },
                )
            path = request.url.params["secretPath"]
            if fail_worker and path == worker.path:
                return httpx.Response(503)
            scope = SCOPE if path == SCOPE.path else worker
            return httpx.Response(
                200,
                json={
                    "secrets": [secret(scope=scope, id="id-" + path[1:])],
                    "imports": [],
                },
            )

        return Infisical(
            config, "fixture-access-token", transport=httpx.MockTransport(handle)
        )

    first = reconcile(source(False).collect())[0]
    second = reconcile(source(True).collect(), first)[0]
    metadata = [e for e in second.evidence if e.observation.key == "infisical.location"]
    assert len(metadata) == 2
    assert {
        json.loads(e.observation.value)["path"]: e.verification for e in metadata
    } == {"/api": "verified", "/worker": "stale"}
    assert second.publication_blocked


def test_metadata_outage_does_not_invalidate_verified_repository_coverage() -> None:
    base = full_estate()
    failure = client(status=503).collect()
    snapshot = reconcile(
        replace(
            base,
            observations=(*base.observations, *failure.observations),
            failures=failure.failures,
            coverage=(*base.coverage, *failure.coverage),
        )
    )[0]
    assert snapshot.estate_coverage.complete
    assert snapshot.publication_blocked
    assert snapshot.estate_state == "blocked"


def test_database_and_known_good_publication_preserve_value_safety_on_outage(
    database_url: str,
) -> None:
    base = full_estate()
    good = client(entries=[secret(secretComment=SENTINEL)]).collect()
    current = replace(
        base,
        observations=(
            *base.observations,
            *good.observations,
            reference(
                project=SCOPE.project, environment=SCOPE.environment, path=SCOPE.path
            ),
        ),
        coverage=(*base.coverage, *good.coverage),
    )
    store = SnapshotStore(database_url)
    snapshot = store.reconcile(current).snapshot
    assert not snapshot.publication_blocked
    drive = DriveService()
    assert publish(snapshot, drive.client(), database_url, "approved-parent").complete
    before = drive.texts.copy()
    request_count = len(drive.requests)
    failed = client(entries=[secret(secretValue=SENTINEL)]).collect()
    outage = replace(
        current,
        observations=tuple(
            o for o in current.observations if not o.key.startswith("infisical.")
        )
        + failed.observations,
        coverage=tuple(
            c for c in current.coverage if not c.source.startswith("infisical:")
        )
        + failed.coverage,
        failures=failed.failures,
    )
    stale = SnapshotStore(database_url).reconcile(outage).snapshot
    assert mappings(stale)[0][0].status == "stale"
    assert not publish(stale, drive.client(), database_url, "approved-parent").complete
    assert drive.texts == before
    assert len(drive.requests) == request_count
    assert SENTINEL not in canonical(drive.texts)
    with connection(database_url, "architecture_snapshot") as db:
        assert all(
            SENTINEL not in row[0]
            for row in db.execute("SELECT payload FROM snapshots").fetchall()
        )
        assert all(
            SENTINEL not in str(row)
            for row in db.execute("SELECT state,reason,sources FROM runs").fetchall()
        )


@pytest.mark.parametrize("credentials", [True, False])
@pytest.mark.parametrize("publish_requested", [True, False])
def test_integration_candidate_uses_live_metadata_and_honest_local_provenance(  # noqa: PLR0915 -- full offline collection/publication/restart workflow
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    credentials: bool,
    publish_requested: bool,
    database_url: str,
) -> None:
    repository = "SpencerRWood/architecture-docs"
    loaded = load_registry(Path("config/repositories.toml"))
    assert loaded.infisical is not None
    estate = EstateContract(
        (ExpectedRepository(repository, "supporting", True),),
        (RequiredDomain("secrets", (NodeKind.SECRET_REFERENCE,)),),
    )
    approved = replace(
        loaded,
        repositories=tuple(r for r in loaded.repositories if r.name == repository),
        estate=estate,
        infisical=replace(
            loaded.infisical,
            bindings=tuple(
                b for b in loaded.infisical.bindings if b.repository == repository
            ),
        ),
    )
    monkeypatch.setattr(
        "architecture_docs.integration.load_registry", lambda _: approved
    )
    ref = reference("ARCHITECTURE_DOCS_DATABASE_URL")
    ref = replace(
        ref,
        collector="integration-worktree",
        provenance=Provenance(repository, "worktree:architecture.toml", None),
    )
    pinned = replace(
        reference("PINNED_TOKEN"),
        provenance=Provenance(repository, "docs/operations.md", "a" * 40),
    )
    original = reconcile(
        CollectionResult(
            (ref, pinned, estate.observation()),
            coverage=(
                SourceCoverage(repository, ref.provenance.source, ref.collector, None),
                SourceCoverage(
                    repository, pinned.provenance.source, pinned.collector, "a" * 40
                ),
            ),
        )
    )[0]
    previous = tmp_path / "previous.json"
    previous.write_text(original.to_json())
    output = tmp_path / "candidate"
    service = DriveService()
    assert publish(original, service.client(), database_url, "parent").complete
    service.requests.clear()
    service.writes.clear()
    monkeypatch.setattr(
        "architecture_docs.integration.GoogleDrive", lambda _: service.client()
    )
    monkeypatch.setenv("ARCHITECTURE_DOCS_DATABASE_URL", database_url)

    def factory(config: InfisicalConfig) -> Infisical:
        def handle(request: httpx.Request) -> httpx.Response:
            if "/workspace/" in request.url.path:
                return httpx.Response(
                    200,
                    json={
                        "workspace": {
                            "id": config.scopes[0].project,
                            "name": "Infrastructure Dev",
                        }
                    },
                )
            query = request.url.params
            scope = Scope(query["projectId"], query["environment"], query["secretPath"])
            key = (
                "ARCHITECTURE_DOCS_DATABASE_URL"
                if scope.path == "/architecture-docs"
                else "UNCONSUMED"
            )
            return httpx.Response(
                200, json={"secrets": [secret(key, scope)], "imports": []}
            )

        return Infisical(
            config,
            "fixture-token" if credentials else None,
            transport=httpx.MockTransport(handle),
        )

    monkeypatch.setattr(Infisical, "from_environment", factory)
    arguments = [str(previous), str(output), "--worktree-contract", "architecture.toml"]
    if publish_requested:
        arguments.extend(["--publish-parent", "parent"])
    status = integration_main(arguments)
    assert status == (0 if credentials else 2)
    report = json.loads((output / "review.json").read_text())
    assert report["live_mapping_verified"] == credentials
    assert report["mapped"] == (1 if credentials else 0)
    assert report["document_count"] == 15
    assert "secrets-manifest" in report["affected_documents"]
    if credentials:
        assert "secret_topology" in report["material_categories"]
    else:
        assert (
            report["material_categories"] == []
        )  # Incomplete evidence defers changes.
    snapshot = snapshot_from_json((output / "snapshot.json").read_text())
    local = [
        e
        for e in snapshot.evidence
        if e.observation.collector == "integration-worktree"
    ]
    assert local
    assert all(e.observation.provenance.revision is None for e in local)
    markdown = (output / "secrets-manifest.md").read_text()
    assert "ARCHITECTURE_DOCS_DATABASE_URL" in snapshot.to_json()
    assert "fixture-token" not in markdown
    if credentials:
        assert "Infrastructure Dev" in markdown
        assert "/architecture-docs" in markdown
        if publish_requested:
            manifest = next(
                a
                for a in report["publication"]["artifacts"]
                if a["artifact"] == "secrets-manifest"
            )
            assert manifest["state"] == "published"
            assert service.texts[manifest["file_id"]] == render(snapshot).to_markdown()
            service.writes.clear()
            # Local no-ops must still consult the published ledger.
            assert (
                integration_main(
                    [
                        str(output / "snapshot.json"),
                        str(output),
                        "--publish-parent",
                        "parent",
                        "--worktree-contract",
                        "architecture.toml",
                    ]
                )
                == 0
            )
            repeated = json.loads((output / "review.json").read_text())
            assert not repeated["affected_documents"]
            assert repeated["publication"]["complete"]
            assert not service.writes
    else:
        assert snapshot.publication_blocked
        assert report["failures"] == ["metadata_credentials_missing"] * 5
    if not credentials or not publish_requested:
        assert not service.requests
        assert report["publication"] is None


def test_integration_rejects_unapproved_local_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    previous = tmp_path / "previous.json"
    previous.write_text(reconcile(full_estate())[0].to_json())
    monkeypatch.setattr(Infisical, "from_environment", Infisical)
    with pytest.raises(SystemExit):
        integration_main(
            [
                str(previous),
                str(tmp_path / "output"),
                "--worktree-contract",
                "/etc/passwd",
            ]
        )
