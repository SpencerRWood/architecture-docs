"""Architecture overview is a view of typed nodes and edges, never repository prose."""

import html
from dataclasses import replace

from architecture_docs.declarations import EdgeKind, NodeKind
from architecture_docs.graph import Edge, Node
from architecture_docs.reconciliation import Snapshot
from architecture_docs.renderers.artifacts import (
    DEFAULT_CONFIG,
    Document,
    DocumentKind,
    RenderConfig,
    Row,
)
from architecture_docs.renderers.views import EDGE_LABELS, View, label, section, sources


def boundary_rows(view: View, node: Node) -> tuple[Row, ...]:
    rows = view.entity_rows(node, ("purpose", "technology", "lifecycle"))
    if node.kind != NodeKind.REPOSITORY:
        return rows
    # Repository existence needs repository evidence, not every contained fact.
    evidence = tuple(
        item
        for item in node.evidence
        if item.observation.provenance.source == "github:repository"
    )
    return (replace(rows[0], sources=sources(evidence or node.evidence[:1])), *rows[1:])


def major_entities(view: View) -> frozenset[str]:
    """Explicit architecture and runtime facts, excluding executable inventory."""
    return frozenset(
        node.id
        for node in view.snapshot.graph.nodes
        if node.declared
        and node.kind
        in {
            NodeKind.REPOSITORY,
            NodeKind.SYSTEM,
            NodeKind.SERVICE,
            NodeKind.ENVIRONMENT,
            NodeKind.HOST,
            NodeKind.DATABASE,
            NodeKind.STORAGE,
            NodeKind.ORCHESTRATION,
            NodeKind.INTERFACE,
        }
        and (
            node.kind not in {NodeKind.STORAGE, NodeKind.ORCHESTRATION}
            or any(
                item.observation.key == "architecture.node" for item in node.evidence
            )
        )
        and (node.kind != NodeKind.SERVICE or major_service(view, node))
        and (
            node.kind != NodeKind.SYSTEM
            or any(
                item.observation.key == "architecture.node" for item in node.evidence
            )
        )
    )


def major_service(view: View, node: Node) -> bool:
    """Compose discovery alone does not establish an estate-level boundary."""
    classifications = {
        "purpose": {
            "application",
            "major",
            "runtime",
            "system-boundary",
            "shared-infrastructure",
        },
        "role": {"application", "major", "runtime", "shared-infrastructure"},
        "technology": {
            "postgresql",
            "database",
            "dagster",
            "reverse-proxy",
            "caddy",
            "traefik",
        },
    }
    if any(
        prop.name in {"deployment_path", "code_location", "reverse_proxy"}
        or any(
            candidate.value in classifications.get(prop.name, set())
            for candidate in prop.candidates
        )
        for prop in node.properties
    ):
        return True
    significant = {
        EdgeKind.DEPENDS_ON,
        EdgeKind.DEPLOYMENT_OWNER,
        EdgeKind.DEPLOYS_TO,
        EdgeKind.HOSTED_BY,
        EdgeKind.READS_DATABASE,
        EdgeKind.USES_STORAGE,
        EdgeKind.ORCHESTRATES,
        EdgeKind.EXPOSES,
        EdgeKind.NOTIFIES,
    }
    for edge in view.snapshot.graph.edges:
        if node.id not in {edge.source, edge.target} or edge.kind not in significant:
            continue
        peer = view.nodes[edge.target if edge.source == node.id else edge.source]
        if not peer.declared or peer.kind in {
            NodeKind.PACKAGE,
            NodeKind.SECRET_REFERENCE,
            NodeKind.PROCEDURE,
        }:
            continue
        if peer.repository != node.repository:
            return True
        if edge.kind in {
            EdgeKind.DEPLOYMENT_OWNER,
            EdgeKind.DEPLOYS_TO,
            EdgeKind.ORCHESTRATES,
            EdgeKind.EXPOSES,
            EdgeKind.NOTIFIES,
        }:
            return True
        if edge.kind == EdgeKind.HOSTED_BY and peer.kind in {
            NodeKind.HOST,
            NodeKind.ENVIRONMENT,
            NodeKind.SYSTEM,
        }:
            return True
        if edge.kind in {EdgeKind.READS_DATABASE, EdgeKind.USES_STORAGE}:
            consumers = {
                view.nodes[item.source].repository
                for item in view.snapshot.graph.edges
                if item.target == peer.id and item.kind == edge.kind
            }
            if len(consumers) > 1:
                return True
    return False


def major_edges(view: View, identities: frozenset[str]) -> tuple[Edge, ...]:
    return tuple(
        edge
        for edge in sorted(view.snapshot.graph.edges, key=lambda edge: edge.id)
        if edge.source in identities
        and edge.target in identities
        and edge.kind not in {EdgeKind.CONSUMES_SECRET, EdgeKind.OPERATED_BY}
    )


def diagram(view: View) -> str | None:
    identities = major_entities(view)
    if not identities:
        return None
    aliases = {
        identity: f"n{index}" for index, identity in enumerate(sorted(identities), 1)
    }
    lines = ["flowchart LR"]
    for identity in sorted(identities):
        node = view.nodes[identity]
        text = label(node) + (" (reference only)" if not node.declared else "")
        lines.append(f'  {aliases[identity]}["{html.escape(text, quote=True)}"]')
    for edge in major_edges(view, identities):
        if edge.source in identities and edge.target in identities:
            state = view.relationship(edge).state
            text = EDGE_LABELS[edge.kind]
            if state.startswith(("drift", "ambiguous")) or "stale" in state:
                text += f" ({state})"
            lines.append(
                f'  {aliases[edge.source]} -->|"{html.escape(text, quote=True)}"| '
                f"{aliases[edge.target]}"
            )
    return "\n".join(lines)


def render(snapshot: Snapshot, config: RenderConfig = DEFAULT_CONFIG) -> Document:
    view = View(snapshot)
    identities = major_entities(view)
    sections = (
        section(
            "boundaries",
            "Repositories and major system boundaries",
            tuple(
                row
                for node in sorted(snapshot.graph.nodes, key=lambda node: node.id)
                if node.id in identities
                for row in boundary_rows(view, node)
            ),
            "No repository or major system declarations are available.",
        ),
        section(
            "relationships",
            "Typed architecture relationships",
            tuple(view.relationship(edge) for edge in major_edges(view, identities)),
            "No architecture relationships are declared; "
            "interaction paths remain unknown.",
        ),
    )
    sections = (replace(sections[0], diagram=diagram(view)), sections[1])
    return view.document(
        DocumentKind.OVERVIEW, "Architecture Overview", sections, config
    )
