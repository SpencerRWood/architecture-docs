"""Metadata-only secret references with explicit ownership and location gaps."""

from dataclasses import replace

from architecture_docs.declarations import SECRET_FIELDS, EdgeKind, NodeKind
from architecture_docs.reconciliation import Snapshot
from architecture_docs.renderers.artifacts import (
    DEFAULT_CONFIG,
    Document,
    DocumentKind,
    RenderConfig,
    Section,
)
from architecture_docs.renderers.views import View, gap, section


def render(snapshot: Snapshot, config: RenderConfig = DEFAULT_CONFIG) -> Document:
    view = View(snapshot)
    entries = []
    for node in snapshot.graph.manifest(NodeKind.SECRET_REFERENCE):
        rows = view.entity_rows(node, tuple(sorted(SECRET_FIELDS)))
        for prop in node.properties:
            if (
                prop.name in {"project", "environment", "path", "injection"}
                and prop.state != "agreed"
            ):
                rows += (
                    gap(
                        f"{node.id}:conflicting:{prop.name}",
                        f"Conflicting secret {prop.name}; location is not unambiguous.",
                        (node.id,),
                    ),
                )
        for field in sorted(SECRET_FIELDS):
            if field == "owner" and any(
                edge.kind == EdgeKind.OWNED_BY and edge.source == node.id
                for edge in snapshot.graph.edges
            ):
                continue
            if not any(prop.name == field for prop in node.properties):
                rows += (
                    gap(
                        f"{node.id}:{field}",
                        f"Secret {field} is not declared.",
                        (node.id,),
                    ),
                )
        consumers = tuple(
            view.relationship(edge)
            for edge in snapshot.graph.edges
            if edge.kind == EdgeKind.CONSUMES_SECRET and edge.target == node.id
        )
        rows += consumers or (
            gap(
                f"{node.id}:consumers", "Secret consumers are not declared.", (node.id,)
            ),
        )
        # A repository namespace identifies where evidence lives, not the owner.
        rows += view.relationships((EdgeKind.OWNED_BY,), frozenset({node.id}))
        entries.append(Section(node.id, node.name, rows))
    if not entries:
        entries.append(
            section(
                "secret-references",
                "Secret references",
                (),
                "No secret references are evidenced; absence does not establish "
                "that no secrets are used.",
            )
        )
    discovery = tuple(
        row
        for node in snapshot.graph.nodes
        for row in view.entity_rows(node, ("secret_gap",))[1:]
    )
    if discovery:
        entries.append(
            Section(
                "discovery-gaps",
                "Secret discovery gaps",
                tuple(replace(row, state="gap") for row in discovery),
            )
        )
    return view.document(
        DocumentKind.SECRETS, "Secrets Manifest", tuple(entries), config
    )
