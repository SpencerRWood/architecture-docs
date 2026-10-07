"""Shared graph views preserve candidates, typed relationships, and gaps."""

from collections import defaultdict
from dataclasses import dataclass, replace
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

DOMAINS = {
    NodeKind.REPOSITORY: "Repository dependencies",
    NodeKind.SYSTEM: "Systems and runtime",
    NodeKind.SERVICE: "Systems and runtime",
    NodeKind.ENVIRONMENT: "Systems and runtime",
    NodeKind.HOST: "Systems and runtime",
    NodeKind.INTERFACE: "Systems and runtime",
    NodeKind.DATABASE: "Data and storage",
    NodeKind.STORAGE: "Data and storage",
    NodeKind.ORCHESTRATION: "Automation and orchestration",
    NodeKind.RELEASE_CONTRACT: "Deployment and release",
    NodeKind.PROCEDURE: "Operational procedures",
    NodeKind.SECRET_REFERENCE: "Secret references",
    NodeKind.OWNER: "Ownership",
    NodeKind.PACKAGE: "Repository dependencies",
}

SUMMARIES = {
    DocumentKind.OVERVIEW: (
        "Major systems, runtime boundaries and significant interactions."
    ),
    DocumentKind.CATALOG: (
        "Comprehensive repository inventory and dependency evidence."
    ),
    DocumentKind.DEPLOYMENT: (
        "Declared delivery stages, deployment ownership and targets."
    ),
    DocumentKind.RUNTIME: (
        "Runtime hosting, interfaces and persistence responsibilities."
    ),
    DocumentKind.DATA: (
        "Database and storage contracts, consumers and recovery references."
    ),
    DocumentKind.AUTOMATION: (
        "Automation capabilities, orchestration and shared delivery."
    ),
    DocumentKind.SECRETS: (
        "Consumer secret references mapped to approved Infisical locations; "
        "metadata only."
    ),
}


def merge_rows(rows: tuple[Row, ...]) -> tuple[Row, ...]:
    """Group equivalent facts without collapsing candidates or source provenance."""
    groups: dict[tuple[str, str, str], list[Row]] = defaultdict(list)
    for row in rows:
        groups[row.label, row.value, row.state].append(row)
    return tuple(
        replace(
            items[0],
            id=digest(
                (
                    key,
                    tuple(sorted({entity for row in items for entity in row.entities})),
                )
            ),
            entities=tuple(
                sorted({entity for row in items for entity in row.entities})
            ),
            sources=tuple(sorted({source for row in items for source in row.sources})),
        )
        for key, items in sorted(groups.items())
    )


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

    def architecture_summary(self, entities: tuple[str, ...]) -> str:
        nodes = [self.nodes[identity] for identity in entities]
        categories = sorted({DOMAINS[node.kind].lower() for node in nodes})
        technologies = sorted(
            {
                candidate.value
                for node in nodes
                for prop in node.properties
                if prop.name == "technology" and prop.state == "agreed"
                for candidate in prop.candidates
            }
        )
        return (
            " Architecture categories: " + ", ".join(categories) + "."
            if categories
            else " No architecture boundaries are evidenced in this view."
        ) + (
            " Declared technologies: " + ", ".join(technologies) + "."
            if technologies
            else ""
        )

    def overview_summary(self, entities: tuple[str, ...]) -> str:
        coverage = self.snapshot.estate_coverage
        represented = [item for item in coverage.repositories if item.represented]
        classifications = ", ".join(
            f"{sum(item.classification == category for item in represented)} {category}"
            for category in ("core", "service", "supporting")
        )
        persistence = sorted(
            {
                f"{self.nodes[identity].repository} / {self.nodes[identity].name}"
                for identity in entities
                if self.nodes[identity].kind in {NodeKind.DATABASE, NodeKind.STORAGE}
            }
        )
        cross_repository = sum(
            edge.source in entities and edge.target in entities
            for edge in self.snapshot.graph.cross_repository_edges()
        )
        material_gaps = (
            len(
                {
                    (DOMAINS[self.nodes[item.entity].kind], item.reason)
                    for item in self.snapshot.graph.gaps
                    if item.entity in entities
                }
            )
            + len(self.snapshot.collection.failures)
            + sum(
                prop.state != "agreed"
                for node in self.snapshot.graph.nodes
                for prop in node.properties
                if prop.name not in self.snapshot.policy.transient_properties
            )
            + sum(
                not item.represented and item.required for item in coverage.repositories
            )
            + sum(not item.repositories for item in coverage.domains)
            + int(not coverage.configured)
        )
        return (
            f" {len(represented)} represented repositories ({classifications})."
            + self.architecture_summary(entities)
            + " Shared persistence boundaries: "
            + ("; ".join(persistence) or "not declared")
            + "."
            + f" {cross_repository} significant cross-repository relationships."
            + f" Estate coverage: {self.snapshot.estate_state}; "
            + f"{material_gaps} material unresolved evidence gaps."
        )

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
        groups: dict[tuple[str, str], list[Node]] = defaultdict(list)
        for item in sorted(self.snapshot.graph.gaps):
            if item.entity in entities:
                node = self.nodes[item.entity]
                if item.reason.startswith("missing_secret_") and all(
                    e.observation.key.startswith("workflow.") for e in node.evidence
                ):
                    continue
                groups[DOMAINS[node.kind], item.reason].append(node)
        rows = [
            Row(
                digest((domain, reason, tuple(node.id for node in nodes))),
                domain,
                reason.replace("_", " ")
                + ": "
                + "; ".join(label(node) for node in nodes),
                "gap",
                tuple(node.id for node in nodes),
                sources(tuple(item for node in nodes for item in node.evidence)),
            )
            for (domain, reason), nodes in sorted(groups.items())
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
                                label(self.nodes[candidate.value])
                                if candidate.value in self.nodes
                                else candidate.value
                                for candidate in prop.candidates
                            )
                            + (
                                "; preferred by precedence: "
                                + (
                                    label(self.nodes[prop.preferred])
                                    if prop.preferred in self.nodes
                                    else prop.preferred
                                )
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
        coverage = self.snapshot.estate_coverage
        coverage_rows = [
            Row(
                "estate:status",
                "Estate coverage",
                self.snapshot.estate_state,
                "verified" if coverage.complete else "gap",
            )
        ]
        if not coverage.configured:
            coverage_rows.append(
                gap(
                    "estate:contract",
                    "Estate coverage expectations are not configured.",
                )
            )
        coverage_rows.extend(
            Row(
                f"estate:repository:{item.name}",
                item.name,
                f"{item.classification}; "
                f"{'required' if item.required else 'optional'}; {item.reason}",
                "verified" if item.represented else "gap",
                sources=self.estate_sources((item.name,)) if item.represented else (),
            )
            for item in coverage.repositories
            if kind == DocumentKind.CATALOG or not item.represented
        )
        coverage_rows.extend(
            Row(
                f"estate:domain:{item.name}",
                item.name,
                ", ".join(item.repositories) or "No current parsed domain evidence",
                "verified" if item.repositories else "gap",
                sources=self.estate_sources(item.repositories),
            )
            for item in coverage.domains
            if kind == DocumentKind.CATALOG or not item.repositories
        )
        sections += (
            Section("estate-coverage", "Estate coverage", tuple(coverage_rows)),
        )
        entities = frozenset(
            entity
            for section in sections
            for row in section.rows
            for entity in row.entities
        )
        diagnostics = self.diagnostics(entities)
        if diagnostics:
            domains: dict[str, list[Row]] = defaultdict(list)
            for row in diagnostics:
                domain = (
                    DOMAINS[self.nodes[row.entities[0]].kind]
                    if row.entities
                    else "Source verification"
                )
                domains[domain].append(row)
            sections += tuple(
                Section(
                    f"evidence-status:{digest(domain)}",
                    f"Evidence gaps: {domain}",
                    tuple(rows),
                )
                for domain, rows in sorted(domains.items())
            )
        blocked = self.snapshot.publication_blocked
        state = (
            "incomplete_estate"
            if not coverage.complete
            else "blocked"
            if blocked
            else "with_gaps"
            if any(row.state == "gap" for section in sections for row in section.rows)
            else "complete"
        )
        fact_entities = tuple(
            sorted(
                {
                    entity
                    for section in sections
                    if not section.id.startswith(("estate-", "evidence-status"))
                    for row in section.rows
                    for entity in row.entities
                }
            )
        )
        summary = Row(
            "summary",
            "Document scope",
            SUMMARIES.get(
                kind, "Evidence-grounded procedure references with explicit phase gaps."
            )
            + (
                self.overview_summary(fact_entities)
                if kind == DocumentKind.OVERVIEW
                else self.architecture_summary(fact_entities)
            )
            + " Missing declarations remain gaps; supporting files alone "
            "do not establish procedures.",
            "derived" if fact_entities else "gap",
        )
        sections = (Section("summary", "Summary", (summary,)), *sections)
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

    def estate_sources(
        self, repositories: tuple[str, ...]
    ) -> tuple[SourceReference, ...]:
        current = set(self.snapshot.collection.observations)
        covered = {
            (item.repository, item.source, item.collector, item.revision)
            for item in self.snapshot.collection.coverage
        }
        return sources(
            tuple(
                item
                for item in self.snapshot.evidence
                if item.verification == "verified"
                and item.observation in current
                and item.observation.collector != "estate"
                and item.observation.provenance.repository in repositories
                and item.observation.provenance.revision is not None
                and (
                    item.observation.provenance.repository,
                    item.observation.provenance.source,
                    item.observation.collector,
                    item.observation.provenance.revision,
                )
                in covered
            )
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
