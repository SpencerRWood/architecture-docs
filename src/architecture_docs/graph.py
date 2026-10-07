"""Evidence-bearing graph and deterministic normalization, independent of rendering."""

import json
from collections import defaultdict
from dataclasses import dataclass
from typing import Literal
from urllib.parse import quote

from architecture_docs.declarations import (
    EdgeKind,
    NodeKind,
    edge_declaration,
    node_declaration,
)
from architecture_docs.model import Observation


@dataclass(frozen=True)
class Evidence:
    observation: Observation
    verification: Literal["verified", "stale"] = "verified"


@dataclass(frozen=True)
class Candidate:
    value: str
    evidence: tuple[Evidence, ...]


@dataclass(frozen=True)
class Property:
    name: str
    candidates: tuple[Candidate, ...]
    preferred: str | None
    state: Literal["agreed", "drift", "ambiguous"]


@dataclass(frozen=True)
class Node:
    id: str
    kind: NodeKind
    repository: str
    name: str
    declared: bool
    properties: tuple[Property, ...]
    evidence: tuple[Evidence, ...]


@dataclass(frozen=True)
class Edge:
    id: str
    kind: EdgeKind
    source: str
    target: str
    evidence: tuple[Evidence, ...]


@dataclass(frozen=True, order=True)
class Gap:
    entity: str
    reason: str


@dataclass(frozen=True)
class Graph:
    nodes: tuple[Node, ...]
    edges: tuple[Edge, ...]
    gaps: tuple[Gap, ...]
    schema_version: int = 1

    def manifest(self, kind: NodeKind) -> tuple[Node, ...]:
        """Typed deterministic manifests are inputs for later renderers."""
        return tuple(node for node in self.nodes if node.kind == kind)

    def cross_repository_edges(self) -> tuple[Edge, ...]:
        repositories = {node.id: node.repository for node in self.nodes}
        return tuple(
            edge
            for edge in self.edges
            if repositories[edge.source] != repositories[edge.target]
        )


def node_id(kind: NodeKind, repository: str, name: str) -> str:
    # Repository nodes unify references from other repositories with collection.
    identity = repository if kind == NodeKind.REPOSITORY else name
    return f"{kind}:{quote(repository, safe='')}:{quote(identity, safe='')}"


def evidence_key(item: Evidence) -> tuple[int, str, str, str, str, str, str, str, str]:
    observation = item.observation
    provenance = observation.provenance
    return (
        observation.authority,
        provenance.repository,
        provenance.source,
        observation.collector,
        observation.key,
        observation.value,
        provenance.revision or "",
        provenance.blob or "",
        item.verification,
    )


def ordered(items: list[Evidence]) -> tuple[Evidence, ...]:
    return tuple(sorted(set(items), key=evidence_key))


def resolve(name: str, values: dict[str, list[Evidence]]) -> Property:
    candidates = tuple(
        Candidate(value, ordered(items)) for value, items in sorted(values.items())
    )
    highest = min(
        item.observation.authority
        for candidate in candidates
        for item in candidate.evidence
    )
    authoritative = [
        candidate.value
        for candidate in candidates
        if any(item.observation.authority == highest for item in candidate.evidence)
    ]
    if len(authoritative) > 1:
        return Property(name, candidates, None, "ambiguous")
    return Property(
        name, candidates, authoritative[0], "drift" if len(candidates) > 1 else "agreed"
    )


class Builder:
    """Only explicit declarations create typed semantics; names imply no hosting."""

    def __init__(self) -> None:
        self.identities: dict[str, tuple[NodeKind, str, str]] = {}
        self.declared: set[str] = set()
        self.nodes: dict[str, list[Evidence]] = defaultdict(list)
        self.properties: dict[tuple[str, str], dict[str, list[Evidence]]] = defaultdict(
            lambda: defaultdict(list)
        )
        self.edges: dict[tuple[EdgeKind, str, str], list[Evidence]] = defaultdict(list)

    def node(
        self,
        kind: NodeKind,
        name: str,
        evidence: Evidence,
        repository: str | None = None,
        *,
        declared: bool = True,
    ) -> str:
        repository = repository or evidence.observation.provenance.repository
        identity = node_id(kind, repository, name)
        if kind == NodeKind.REPOSITORY:
            name = repository
        self.identities[identity] = (kind, repository, name)
        self.nodes[identity].append(evidence)
        if declared:
            self.declared.add(identity)
        return identity

    def property(
        self, identity: str, name: str, value: str, evidence: Evidence
    ) -> None:
        self.properties[identity, name][value].append(evidence)

    def edge(
        self, kind: EdgeKind, source: str, target: str, evidence: Evidence
    ) -> None:
        self.edges[kind, source, target].append(evidence)
        if kind in {EdgeKind.HOSTED_BY, EdgeKind.DEPLOYMENT_OWNER, EdgeKind.OWNED_BY}:
            self.property(source, f"relationship:{kind}", target, evidence)

    def finish(self) -> Graph:
        nodes = tuple(
            Node(
                identity,
                *self.identities[identity],
                identity in self.declared,
                tuple(
                    resolve(name, values)
                    for (subject, name), values in sorted(self.properties.items())
                    if subject == identity
                ),
                ordered(items),
            )
            for identity, items in sorted(self.nodes.items())
        )
        edges = tuple(
            Edge(
                f"{kind}:{quote(source, safe='')}:{quote(target, safe='')}",
                kind,
                source,
                target,
                ordered(items),
            )
            for (kind, source, target), items in sorted(self.edges.items())
        )
        gaps = []
        for node in nodes:
            if not node.declared:
                gaps.append(Gap(node.id, "reference_only"))
            if node.kind == NodeKind.SECRET_REFERENCE:
                present = {prop.name for prop in node.properties}
                for field in ("project", "environment", "path", "injection"):
                    if field not in present:
                        gaps.append(Gap(node.id, f"missing_secret_{field}"))
        return Graph(nodes, edges, tuple(sorted(gaps)))


def explicit(builder: Builder, item: Evidence) -> None:
    observation = item.observation
    data = json.loads(observation.value)
    if observation.key == "architecture.node":
        declaration = node_declaration(data)
        repository = observation.provenance.repository
        if (
            declaration["kind"] == NodeKind.REPOSITORY
            and declaration["name"] != repository
        ):
            raise ValueError("repository declaration identity mismatch")
        identity = builder.node(
            NodeKind(declaration["kind"]), declaration["name"], item
        )
        root = node_id(NodeKind.REPOSITORY, repository, repository)
        if identity != root:
            builder.edge(EdgeKind.CONTAINS, root, identity, item)
        for field, value in declaration["attributes"].items():
            builder.property(identity, field, value, item)
        return
    declaration = edge_declaration(data)
    endpoints = []
    for side in ("source", "target"):
        ref = declaration[side]
        endpoints.append(
            builder.node(
                NodeKind(ref["kind"]),
                ref["name"],
                item,
                ref.get("repository"),
                declared=False,
            )
        )
    builder.edge(EdgeKind(declaration["kind"]), endpoints[0], endpoints[1], item)


def child(
    builder: Builder,
    repository: str,
    kind: NodeKind,
    name: str,
    item: Evidence,
) -> str:
    identity = builder.node(kind, name, item)
    builder.edge(EdgeKind.CONTAINS, repository, identity, item)
    return identity


def workflow(builder: Builder, repository: str, item: Evidence) -> None:
    observation = item.observation
    source = child(
        builder, repository, NodeKind.ORCHESTRATION, observation.provenance.source, item
    )
    if observation.key == "workflow.secret_name":
        target = builder.node(NodeKind.SECRET_REFERENCE, observation.value, item)
        builder.edge(EdgeKind.CONSUMES_SECRET, source, target, item)
    elif observation.key in {
        "workflow.input_ref",
        "workflow.secret_parameter",
        "workflow.call",
        "workflow.secret_ref",
        "workflow.reusable",
    }:
        # Typed call/usage metadata is resolved by the consumer mapper. It must
        # not create synthetic workflow jobs named after JSON payloads.
        return
    elif observation.key == "workflow.secret_gap":
        builder.property(
            source, f"secret_gap:{observation.value}", observation.value, item
        )
    elif observation.key == "workflow.uses":
        value = observation.value.split("@", 1)[0]
        parts = value.split("/")
        if len(parts) >= 2 and not value.startswith("./"):
            target_repository = "/".join(parts[:2])
            target = builder.node(
                NodeKind.REPOSITORY,
                target_repository,
                item,
                target_repository,
                declared=False,
            )
            builder.edge(EdgeKind.DEPENDS_ON, repository, target, item)
            target_workflow = builder.node(
                NodeKind.ORCHESTRATION, value, item, target_repository, declared=False
            )
        else:
            target_workflow = builder.node(
                NodeKind.ORCHESTRATION, value, item, declared=False
            )
        builder.edge(EdgeKind.CONSUMES_WORKFLOW, source, target_workflow, item)
        builder.property(source, f"workflow:{value}", observation.value, item)
    else:
        child(
            builder,
            source,
            NodeKind.ORCHESTRATION,
            f"{observation.provenance.source}#{observation.value}",
            item,
        )
    if observation.provenance.blob:
        builder.property(source, "source_digest", observation.provenance.blob, item)


def operational_fact(builder: Builder, repository: str, item: Evidence) -> None:
    observation = item.observation
    key, value = observation.key, observation.value
    if key == "terraform.declaration":
        _, resource_type, name = value.split(":", 2)
        if resource_type == "postgresql_database":
            child(builder, repository, NodeKind.DATABASE, name, item)
        else:
            target = child(
                builder, repository, NodeKind.SYSTEM, f"{resource_type}:{name}", item
            )
            builder.property(target, "declaration", value, item)
    elif key in {"contract.kind", "shell.executable", "python.declaration"}:
        target = child(
            builder, repository, NodeKind.PROCEDURE, observation.provenance.source, item
        )
        field = f"{key}:{value}" if key != "contract.kind" else key
        builder.property(target, field, value, item)
        if observation.provenance.blob:
            builder.property(target, "source_digest", observation.provenance.blob, item)
    elif key.startswith("ansible."):
        target = child(
            builder,
            repository,
            NodeKind.ORCHESTRATION,
            observation.provenance.source,
            item,
        )
        builder.property(target, f"{key}:{value}", value, item)
    elif key.startswith("repository."):
        builder.property(repository, key, value, item)
    elif key == "documentation.repository_reference":
        target = builder.node(NodeKind.REPOSITORY, value, item, value, declared=False)
        builder.property(
            target, f"reference:{observation.provenance.source}", value, item
        )


def source_fact(builder: Builder, repository: str, item: Evidence) -> None:
    observation = item.observation
    key, value = observation.key, observation.value
    if key in {"architecture.node", "architecture.edge"}:
        explicit(builder, item)
    elif key.startswith("workflow."):
        workflow(builder, repository, item)
    elif key.startswith("service."):
        _, name, field = key.split(".", 2)
        source = child(builder, repository, NodeKind.SERVICE, name, item)
        if field == "depends_on":
            target = builder.node(NodeKind.SERVICE, value, item, declared=False)
            builder.edge(EdgeKind.DEPENDS_ON, source, target, item)
        elif field == "volume":
            target = builder.node(NodeKind.STORAGE, value, item, declared=False)
            builder.edge(EdgeKind.USES_STORAGE, source, target, item)
        elif field == "environment_name":
            # Environment variable names alone are not evidence of secret use.
            builder.property(source, f"environment_name:{value}", value, item)
    elif key == "compose.services":
        child(builder, repository, NodeKind.SERVICE, value, item)
    elif key == "compose.volumes":
        child(builder, repository, NodeKind.STORAGE, value, item)
    elif key.startswith("package."):
        target = child(builder, repository, NodeKind.PACKAGE, value, item)
        if key != "package.name":
            builder.edge(EdgeKind.DEPENDS_ON, repository, target, item)
    elif key.startswith(
        ("release.", "validation.", "build.", "container.", "dagster.")
    ):
        target = child(
            builder,
            repository,
            NodeKind.RELEASE_CONTRACT,
            observation.provenance.source,
            item,
        )
        # Checks are set-valued; booleans and other contract fields are scalar.
        field = (
            f"{key}:{value}"
            if key.endswith(".checks") or key == "container.base_image"
            else key
        )
        builder.property(target, field, value, item)
    else:
        operational_fact(builder, repository, item)
    # Inventory and supplemental releases stay in snapshot evidence, not topology.


def normalize(evidence: tuple[Evidence, ...]) -> Graph:
    builder = Builder()
    for item in sorted(evidence, key=evidence_key):
        if item.observation.key == "estate.contract" or item.observation.key.startswith(
            "infisical."
        ):
            continue
        repository = item.observation.provenance.repository
        identity = builder.node(NodeKind.REPOSITORY, repository, item)
        source_fact(builder, identity, item)
    return builder.finish()
