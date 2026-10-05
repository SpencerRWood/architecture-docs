"""Shared graph views preserve candidates, typed relationships, and gaps."""

from dataclasses import dataclass
from functools import cached_property

from architecture_docs.declarations import EdgeKind, NodeKind
from architecture_docs.graph import Edge, Evidence, Node
from architecture_docs.reconciliation import Snapshot
from architecture_docs.renderers.artifacts import (
    Document,
    DocumentKind,
    RenderConfig,
    Row,
    Section,
    SourceReference,
    digest,
)

EDGE_LABELS = {
    EdgeKind.CONTAINS: "contains",
    EdgeKind.DEPENDS_ON: "depends on",
    EdgeKind.DEPLOYMENT_OWNER: "declares deployment ownership in",
    EdgeKind.DEPLOYS_TO: "declares deployment to",
    EdgeKind.CONSUMES_WORKFLOW: "consumes workflow",
    EdgeKind.HOSTED_BY: "declares hosting on",
    EdgeKind.READS_DATABASE: "declares data access to",
    EdgeKind.USES_STORAGE: "uses storage",
    EdgeKind.NOTIFIES: "declares notifications to",
    EdgeKind.CONSUMES_SECRET: "references secret",
    EdgeKind.ORCHESTRATES: "orchestrates",
    EdgeKind.EXPOSES: "exposes interface",
    EdgeKind.OWNED_BY: "declares ownership by",
    EdgeKind.OPERATED_BY: "has operational procedure",
}


def sources(evidence: tuple[Evidence, ...]) -> tuple[SourceReference, ...]:
    return tuple(sorted({SourceReference.from_evidence(item) for item in evidence}))


def freshness(evidence: tuple[Evidence, ...]) -> str:
    return (
        "stale"
        if any(item.verification == "stale" for item in evidence)
        else "verified"
    )


def label(node: Node) -> str:
    if node.kind == NodeKind.REPOSITORY:
        return f"repository {node.repository}"
    return f"{node.kind.value.replace('_', ' ')} {node.repository} / {node.name}"


def gap(identity: str, message: str, entities: tuple[str, ...] = ()) -> Row:
    return Row(f"gap:{identity}", "Documentation gap", message, "gap", entities)


@dataclass(frozen=True)
class View:
    snapshot: Snapshot

    def __post_init__(self) -> None:
        if self.snapshot.schema_version != 1 or self.snapshot.graph.schema_version != 1:
            raise ValueError("unsupported rendering model schema")

    @cached_property
    def nodes(self) -> dict[str, Node]:
        return {node.id: node for node in self.snapshot.graph.nodes}

    def entity_rows(
        self, node: Node, fields: tuple[str, ...] | None = None
    ) -> tuple[Row, ...]:
        rows = [
            Row(
                f"entity:{node.id}",
                label(node),
                "Declared entity"
                if node.declared
                else "Reference only; no declaration collected",
                freshness(node.evidence),
                (node.id,),
                sources(node.evidence),
            )
        ]
        for prop in sorted(node.properties, key=lambda item: item.name):
            if (
                prop.name in self.snapshot.policy.transient_properties
                or prop.name == "source_digest"
            ):
                continue
            if fields is not None and not matches(prop.name, fields):
                continue
            for candidate in sorted(prop.candidates, key=lambda item: item.value):
                status: str = prop.state
                if status == "drift":
                    status += (
                        ": preferred"
                        if candidate.value == prop.preferred
                        else ": alternative"
                    )
                value = (
                    label(self.nodes[candidate.value])
                    if prop.name.startswith("relationship:")
                    and candidate.value in self.nodes
                    else candidate.value
                )
                rows.append(
                    Row(
                        digest((node.id, prop.name, candidate.value)),
                        f"{node.name} / {prop.name}",
                        value,
                        f"{status}; {freshness(candidate.evidence)}",
                        (node.id,),
                        sources(candidate.evidence),
                    )
                )
        return tuple(rows)

    def relationship(self, edge: Edge) -> Row:
        nodes = self.nodes
        source, target = nodes[edge.source], nodes[edge.target]
        prop = next(
            (
                prop
                for prop in source.properties
                if prop.name == f"relationship:{edge.kind}"
            ),
            None,
        )
        state: str = prop.state if prop else "declared"
        if prop and state == "drift":
            state += ": preferred" if prop.preferred == edge.target else ": alternative"
        return Row(
            f"edge:{edge.id}",
            edge.kind.value,
            f"{label(source)} {EDGE_LABELS[edge.kind]} {label(target)}",
            f"{state}; {freshness(edge.evidence)}",
            (source.id, target.id),
            sources(edge.evidence),
        )

    def relationships(
        self,
        kinds: tuple[EdgeKind, ...] | None = None,
        entities: frozenset[str] | None = None,
    ) -> tuple[Row, ...]:
        return tuple(
            self.relationship(edge)
            for edge in sorted(self.snapshot.graph.edges, key=lambda edge: edge.id)
            if (kinds is None or edge.kind in kinds)
            and (entities is None or edge.source in entities or edge.target in entities)
        )

    def entities(
        self,
        kinds: tuple[NodeKind, ...],
        fields: tuple[str, ...] | None = None,
    ) -> tuple[Row, ...]:
        return tuple(
            row
            for node in sorted(self.snapshot.graph.nodes, key=lambda node: node.id)
            if node.kind in kinds
            for row in self.entity_rows(node, fields)
        )

    def properties(self, fields: tuple[str, ...]) -> tuple[Row, ...]:
        # Missing lifecycle stages remain gaps, not invented entities.
        return tuple(
            row
            for node in sorted(self.snapshot.graph.nodes, key=lambda node: node.id)
            for row in self.entity_rows(node, fields)[1:]
        )

    def diagnostics(self, entities: frozenset[str]) -> tuple[Row, ...]:
        rows = [
            gap(
                f"{item.entity}:{item.reason}",
                item.reason.replace("_", " "),
                (item.entity,),
            )
            for item in sorted(self.snapshot.graph.gaps)
            if item.entity in entities
        ]
        for node in sorted(self.snapshot.graph.nodes, key=lambda node: node.id):
            if node.id not in entities:
                continue
            for prop in sorted(node.properties, key=lambda prop: prop.name):
                if (
                    prop.name not in self.snapshot.policy.transient_properties
                    and prop.state != "agreed"
                ):
                    rows.append(
                        Row(
                            f"conflict:{node.id}:{prop.name}",
                            f"Conflict: {node.name} / {prop.name}",
                            "Competing candidates: "
                            + ", ".join(
                                candidate.value for candidate in prop.candidates
                            )
                            + (
                                f"; preferred by precedence: {prop.preferred}"
                                if prop.preferred
                                else "; no preferred value"
                            ),
                            prop.state,
                            (node.id,),
                            sources(
                                tuple(
                                    item
                                    for candidate in prop.candidates
                                    for item in candidate.evidence
                                )
                            ),
                        )
                    )
            if freshness(node.evidence) == "stale":
                rows.append(
                    Row(
                        f"stale:{node.id}",
                        label(node),
                        "Prior known-good evidence retained; "
                        "current verification unavailable",
                        "stale",
                        (node.id,),
                        sources(node.evidence),
                    )
                )
        for failure in self.snapshot.collection.failures:
            rows.append(
                Row(
                    digest(
                        (
                            "failure",
                            failure.collector,
                            failure.provenance.repository,
                            failure.provenance.source,
                            failure.reason,
                        )
                    ),
                    "Source verification failure",
                    f"{failure.provenance.repository} / "
                    f"{failure.provenance.source}: {failure.reason}",
                    "blocked",
                )
            )
        return tuple(sorted(rows, key=lambda row: row.id))

    def document(
        self,
        kind: DocumentKind,
        title: str,
        sections: tuple[Section, ...],
        config: RenderConfig,
    ) -> Document:
        entities = frozenset(
            entity
            for section in sections
            for row in section.rows
            for entity in row.entities
        )
        diagnostics = self.diagnostics(entities)
        if diagnostics:
            sections += (
                Section(
                    "evidence-status", "Drift, ambiguity and evidence gaps", diagnostics
                ),
            )
        blocked = self.snapshot.publication_blocked
        state = (
            "blocked"
            if blocked
            else "with_gaps"
            if any(row.state == "gap" for section in sections for row in section.rows)
            else "complete"
        )
        return Document(
            kind,
            f"{config.title_prefix}{title}",
            self.snapshot.id,
            self.snapshot.schema_version,
            self.snapshot.graph.schema_version,
            config.id,
            sections,
            blocked,
            state,
        )


def matches(field: str, prefixes: tuple[str, ...]) -> bool:
    return any(
        field == prefix
        or field.startswith(f"{prefix}:")
        or field.startswith(f"{prefix}.")
        for prefix in prefixes
    )


def section(identity: str, title: str, rows: tuple[Row, ...], missing: str) -> Section:
    return Section(identity, title, rows or (gap(identity, missing),))
