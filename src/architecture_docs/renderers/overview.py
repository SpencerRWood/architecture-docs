"""Architecture overview is a view of typed nodes and edges, never repository prose."""

import html
from dataclasses import replace

from architecture_docs.declarations import NodeKind
from architecture_docs.reconciliation import Snapshot
from architecture_docs.renderers.artifacts import (
    DEFAULT_CONFIG,
    Document,
    DocumentKind,
    RenderConfig,
    digest,
)
from architecture_docs.renderers.views import EDGE_LABELS, View, label, section


def diagram(view: View) -> str | None:
    identities = {
        node.id
        for node in view.snapshot.graph.nodes
        if node.kind in {NodeKind.REPOSITORY, NodeKind.SYSTEM, NodeKind.SERVICE}
    }
    identities.update(
        endpoint
        for edge in view.snapshot.graph.cross_repository_edges()
        for endpoint in (edge.source, edge.target)
    )
    if not identities:
        return None
    aliases = {identity: f"n{digest(identity)}" for identity in identities}
    lines = ["flowchart LR"]
    for identity in sorted(identities):
        node = view.nodes[identity]
        text = label(node) + (" (reference only)" if not node.declared else "")
        lines.append(f'  {aliases[identity]}["{html.escape(text, quote=True)}"]')
    for edge in sorted(view.snapshot.graph.edges, key=lambda edge: edge.id):
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
    sections = (
        section(
            "boundaries",
            "Repositories and major system boundaries",
            view.entities(
                (NodeKind.REPOSITORY, NodeKind.SYSTEM, NodeKind.SERVICE),
                ("purpose", "technology", "lifecycle", "owner"),
            ),
            "No repository or major system declarations are available.",
        ),
        section(
            "relationships",
            "Typed architecture relationships",
            view.relationships(),
            "No architecture relationships are declared; "
            "interaction paths remain unknown.",
        ),
    )
    sections = (replace(sections[0], diagram=diagram(view)), sections[1])
    return view.document(
        DocumentKind.OVERVIEW, "Architecture Overview", sections, config
    )
