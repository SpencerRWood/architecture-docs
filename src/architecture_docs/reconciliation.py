"""Restart-independent evidence reconciliation and material-change classification."""

import json
import tomllib
from dataclasses import asdict, dataclass, replace
from functools import cached_property
from hashlib import sha256
from pathlib import Path
from typing import Literal

from architecture_docs.config import Repository
from architecture_docs.declarations import (
    FIELDS,
    EdgeKind,
    NodeKind,
    canonical,
    identifier,
)
from architecture_docs.estate import EstateCoverage, evaluate
from architecture_docs.graph import Evidence, Graph, Node, evidence_key, normalize
from architecture_docs.model import CollectionResult, Failure, Observation


@dataclass(frozen=True, order=True)
class Tombstone:
    repository: str
    source: str | None = None
    reason: str = "configured_removal"

    def __post_init__(self) -> None:
        Repository(self.repository, ("*",))
        identifier(self.reason)
        if self.source is not None:
            identifier(self.source)


@dataclass(frozen=True)
class Policy:
    transient_properties: tuple[str, ...] = ("collected_at", "last_seen")
    tombstones: tuple[Tombstone, ...] = ()

    def __post_init__(self) -> None:
        if set(self.transient_properties) - FIELDS:
            raise ValueError("unknown transient property")


DEFAULT_POLICY = Policy()


def load_policy(path: Path) -> Policy:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        if (
            set(data) - {"version", "transient_properties", "tombstones"}
            or data.get("version") != 1
        ):
            raise ValueError("unsupported reconciliation policy")
        fields = data.get(
            "transient_properties", list(DEFAULT_POLICY.transient_properties)
        )
        rules = data.get("tombstones", [])
        if not isinstance(fields, list) or not isinstance(rules, list):
            raise ValueError("invalid policy shape")
        return Policy(tuple(fields), tuple(Tombstone(**rule) for rule in rules))
    except OSError, ValueError, TypeError:
        raise ValueError("invalid reconciliation policy") from None


@dataclass(frozen=True)
class Snapshot:
    evidence: tuple[Evidence, ...]
    graph: Graph
    collection: CollectionResult
    policy: Policy
    schema_version: int = 1

    @property
    def successful(self) -> bool:
        """Architecture baseline eligibility, distinct from publication coverage."""
        archived = {
            item.observation.provenance.repository
            for item in self.evidence
            if item.verification == "verified"
            and item.observation.key == "repository.archived"
            and item.observation.value == "true"
        }
        return self.collection.complete and all(
            item.verification == "verified"
            or item.observation.provenance.repository in archived
            for item in self.evidence
        )

    def to_json(self) -> str:
        return canonical(asdict(self))

    @property
    def id(self) -> str:
        return sha256(self.to_json().encode()).hexdigest()

    @property
    def publication_blocked(self) -> bool:
        """Fail closed for downstream publishers; this layer performs no publication."""
        from architecture_docs.secret_mappings import mappings  # noqa: PLC0415

        return (
            any(item.status == "ambiguous" for item in mappings(self)[0])
            or not self.estate_coverage.complete
            or not self.collection.complete
            or any(item.verification != "verified" for item in self.evidence)
            or any(
                prop.state == "ambiguous"
                for node in self.graph.nodes
                for prop in node.properties
            )
        )

    @cached_property
    def estate_coverage(self) -> EstateCoverage:
        verified = {
            item.observation
            for item in self.evidence
            if item.verification == "verified"
        }
        current = replace(
            self.collection,
            observations=tuple(
                item
                for item in self.collection.observations
                if item.key == "estate.contract" or item in verified
            ),
        )
        return evaluate(current)

    @property
    def estate_state(self) -> str:
        if not self.estate_coverage.complete:
            return "incomplete_estate"
        if self.publication_blocked:
            return "blocked"
        return "complete_with_gaps" if self.graph.gaps else "complete"


@dataclass(frozen=True, order=True)
class Change:
    entity: str
    operation: Literal["created", "removed", "changed"]
    category: str
    before: str | None
    after: str | None


@dataclass(frozen=True)
class Diff:
    previous: str | None
    current: str
    changes: tuple[Change, ...]
    schema_version: int = 1

    @property
    def material(self) -> bool:
        return bool(self.changes)

    def to_json(self) -> str:
        return canonical(asdict(self))


def source_matches(source: str, scope: str) -> bool:
    return source == scope or source.startswith(f"{scope}/")


def unavailable(observation: Observation, failures: tuple[Failure, ...]) -> bool:
    provenance = observation.provenance
    return any(
        failure.provenance.repository == provenance.repository
        and (
            (
                failure.collector == "github_source"
                and not provenance.source.startswith("infisical:")
            )
            or (
                failure.collector == observation.collector
                and failure.provenance.source == "github:tree"
            )
            or (
                source_matches(provenance.source, failure.provenance.source)
                and failure.collector in {observation.collector, "github_content"}
            )
        )
        for failure in failures
    )


def removed(  # noqa: PLR0911 - explicit evidence boundaries fail closed independently
    observation: Observation, collection: CollectionResult, policy: Policy
) -> bool:
    provenance = observation.provenance
    if observation.key == "estate.contract":
        return True
    if any(
        rule.repository == provenance.repository
        and (rule.source is None or source_matches(provenance.source, rule.source))
        for rule in policy.tombstones
    ):
        return True
    if unavailable(observation, collection.failures):
        return False
    if (
        observation.key.startswith("classification.")
        or observation.key in {"component.name", "component.description"}
    ) and not any(
        item.key == observation.key
        and item.provenance.repository == provenance.repository
        and item.provenance.source == provenance.source
        for item in collection.observations
    ):
        # An omitted metadata field is a coverage gap, not reclassification.
        return False
    if any(
        scope.repository == provenance.repository
        and scope.collector == observation.collector
        and source_matches(provenance.source, scope.source)
        for scope in collection.coverage
    ):
        return True
    if provenance.source.startswith("infisical:"):
        return False  # Git trees cannot attest secret-manager absence.
    # Only a complete tree within unchanged/current approvals proves file absence.
    return any(
        inventory.repository == provenance.repository
        and not provenance.source.startswith("github:")
        and Repository(inventory.repository, inventory.approvals).approves(
            provenance.source
        )
        and provenance.source not in inventory.paths
        for inventory in collection.inventories
    )


def stable_collection(collection: CollectionResult) -> CollectionResult:
    if collection.schema_version != 1:
        raise ValueError("unsupported collection schema")
    inventories = set(collection.inventories)
    if len({item.repository for item in inventories}) != len(inventories):
        raise ValueError("conflicting repository inventories")
    coverage = set(collection.coverage)
    if len(
        {(item.repository, item.source, item.collector) for item in coverage}
    ) != len(coverage):
        raise ValueError("conflicting source coverage")
    return CollectionResult(
        tuple(
            sorted(
                set(collection.observations),
                key=lambda item: evidence_key(Evidence(item)),
            )
        ),
        tuple(
            sorted(set(collection.failures), key=lambda item: canonical(asdict(item)))
        ),
        tuple(sorted(set(collection.skipped))),
        coverage=tuple(
            sorted(set(collection.coverage), key=lambda item: canonical(asdict(item)))
        ),
        inventories=tuple(
            sorted(
                {
                    type(item)(
                        item.repository,
                        item.revision,
                        tuple(sorted(set(item.paths))),
                        tuple(sorted(set(item.approvals))),
                    )
                    for item in collection.inventories
                },
                key=lambda item: canonical(asdict(item)),
            )
        ),
    )


def reconcile(
    collection: CollectionResult,
    previous: Snapshot | None = None,
    policy: Policy = DEFAULT_POLICY,
) -> tuple[Snapshot, Diff]:
    collection = stable_collection(collection)
    policy = Policy(
        tuple(sorted(set(policy.transient_properties))),
        tuple(sorted(set(policy.tombstones), key=lambda rule: canonical(asdict(rule)))),
    )
    current = {
        observation: Evidence(observation)
        for observation in collection.observations
        if not unavailable(observation, collection.failures)
        and not any(
            rule.repository == observation.provenance.repository
            and (
                rule.source is None
                or source_matches(observation.provenance.source, rule.source)
            )
            for rule in policy.tombstones
        )
    }
    if previous is not None:
        for item in previous.evidence:
            observation = item.observation
            if observation not in current and not removed(
                observation, collection, policy
            ):
                current[observation] = Evidence(observation, "stale")
    evidence = tuple(sorted(current.values(), key=evidence_key))
    snapshot = Snapshot(evidence, normalize(evidence), collection, policy)
    return snapshot, compare(previous, snapshot)


NODE_CATEGORIES = {
    NodeKind.REPOSITORY: "repository",
    NodeKind.SYSTEM: "system",
    NodeKind.DATABASE: "data_storage",
    NodeKind.STORAGE: "data_storage",
    NodeKind.SECRET_REFERENCE: "secret_topology",
    NodeKind.RELEASE_CONTRACT: "release_contract",
    NodeKind.PROCEDURE: "runbook",
    NodeKind.OWNER: "ownership",
    NodeKind.HOST: "deployment_path",
    NodeKind.ENVIRONMENT: "deployment_path",
    NodeKind.ORCHESTRATION: "orchestration",
    NodeKind.INTERFACE: "interface",
    NodeKind.SERVICE: "system",
    NodeKind.PACKAGE: "dependency",
}
EDGE_CATEGORIES = {
    EdgeKind.DEPENDS_ON: "dependency",
    EdgeKind.CONSUMES_WORKFLOW: "dependency",
    EdgeKind.DEPLOYMENT_OWNER: "deployment_path",
    EdgeKind.DEPLOYS_TO: "deployment_path",
    EdgeKind.HOSTED_BY: "deployment_path",
    EdgeKind.READS_DATABASE: "data_storage",
    EdgeKind.USES_STORAGE: "data_storage",
    EdgeKind.CONSUMES_SECRET: "secret_topology",
    EdgeKind.OPERATED_BY: "runbook",
    EdgeKind.OWNED_BY: "ownership",
    EdgeKind.ORCHESTRATES: "orchestration",
    EdgeKind.EXPOSES: "interface",
    EdgeKind.NOTIFIES: "interface",
    EdgeKind.CONTAINS: "membership",
}


def semantic_node(node: Node, policy: Policy) -> str:
    """Exclude revisions, blob hashes, verification, and configured transient fields."""
    return canonical(
        {
            "kind": node.kind,
            "repository": node.repository,
            "name": node.name,
            "declared": node.declared,
            "properties": [
                {
                    "name": prop.name,
                    "values": [candidate.value for candidate in prop.candidates],
                    "preferred": prop.preferred,
                    "state": prop.state,
                }
                for prop in node.properties
                if prop.name not in policy.transient_properties
            ],
        }
    )


def changed_categories(before: str, after: str, default: str) -> tuple[str, ...]:
    fields = []
    for payload in (before, after):
        fields.append(
            {
                prop["name"]: canonical(prop)
                for prop in json.loads(payload).get("properties", [])
            }
        )
    categories = set()
    for field in fields[0].keys() | fields[1].keys():
        if fields[0].get(field) == fields[1].get(field):
            continue
        if field.startswith("classification."):
            categories.add("reclassification")
        elif field == "repository.archived":
            categories.add("archival")
        elif field.startswith(("deployment.", "environment.", "runtime.")):
            categories.add("deployment_topology")
        elif field in {"host", "environment", "deployment_path"} or field.startswith(
            ("relationship:hosted_by", "relationship:deployment_owner")
        ):
            categories.add("deployment_path")
        elif field in {
            "script",
            "prerequisite",
            "verification",
            "rollback",
            "recovery",
        }:
            categories.add("runbook")
        else:
            categories.add(default)
    return tuple(sorted(categories or {default}))


def compare(previous: Snapshot | None, current: Snapshot) -> Diff:
    from architecture_docs.secret_mappings import mappings  # noqa: PLC0415

    before: dict[str, tuple[str, str]] = {}
    after: dict[str, tuple[str, str]] = {}
    for snapshot, target in ((previous, before), (current, after)):
        if snapshot is None or not current.successful:
            continue
        target.update(
            {
                node.id: (
                    NODE_CATEGORIES[node.kind],
                    semantic_node(node, current.policy),
                )
                for node in snapshot.graph.nodes
            }
        )
        target.update(
            {
                edge.id: (
                    EDGE_CATEGORIES[edge.kind],
                    canonical(
                        {
                            "kind": edge.kind,
                            "source": edge.source,
                            "target": edge.target,
                        }
                    ),
                )
                for edge in snapshot.graph.edges
            }
        )
        metadata = sorted(
            {
                canonical((e.observation.value, e.verification))
                for e in snapshot.evidence
                if e.observation.key in {"infisical.location", "secret.location"}
            }
        )
        bindings = sorted(
            {
                e.observation.value
                for e in snapshot.evidence
                if e.observation.key == "infisical.contract"
            }
        )
        if metadata or bindings:
            target["infisical-metadata"] = (
                "secret_topology",
                canonical({"locations": metadata, "approvals": bindings}),
            )
        entries, _ = mappings(snapshot)
        if entries:
            target["secret-mappings"] = (
                "secret_topology",
                canonical(
                    {
                        "mappings": [
                            {
                                "repository": entry.repository,
                                "consumer": entry.consumer,
                                "consumer_name": entry.consumer_name,
                                "locations": [
                                    asdict(loc) for loc, _ in entry.locations
                                ],
                                "injection": entry.injection,
                                "required": entry.required,
                                "owner": entry.owner,
                                "status": entry.status,
                                "provider": entry.provider,
                                "target": entry.target,
                                "via": entry.via,
                            }
                            for entry in entries
                        ]
                    }
                ),
            )
    changes = []
    old_coverage = canonical(asdict(previous.estate_coverage)) if previous else None
    new_coverage = canonical(asdict(current.estate_coverage))
    if old_coverage != new_coverage:
        changes.append(
            Change(
                "estate",
                "changed" if previous else "created",
                "estate_coverage",
                old_coverage,
                new_coverage,
            )
        )
    for identity in sorted(before.keys() | after.keys()):
        old, new = before.get(identity), after.get(identity)
        if old == new:
            continue
        category = after[identity][0] if identity in after else before[identity][0]
        operation: Literal["created", "removed", "changed"] = (
            "created" if old is None else "removed" if new is None else "changed"
        )
        categories = (
            changed_categories(old[1], new[1], category) if old and new else (category,)
        )
        for affected in categories:
            changes.append(
                Change(
                    identity,
                    operation,
                    affected,
                    old[1] if old else None,
                    new[1] if new else None,
                )
            )
    return Diff(previous.id if previous else None, current.id, tuple(changes))
