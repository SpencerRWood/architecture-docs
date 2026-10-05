"""Declared databases, schemas, storage consumers, and backup/recovery references."""

from architecture_docs.declarations import EdgeKind, NodeKind
from architecture_docs.reconciliation import Snapshot
from architecture_docs.renderers.artifacts import (
    DEFAULT_CONFIG,
    Document,
    DocumentKind,
    RenderConfig,
)
from architecture_docs.renderers.views import View, section


def render(snapshot: Snapshot, config: RenderConfig = DEFAULT_CONFIG) -> Document:
    view = View(snapshot)
    data_ids = frozenset(
        node.id
        for node in snapshot.graph.nodes
        if node.kind in {NodeKind.DATABASE, NodeKind.STORAGE}
    )
    sections = (
        section(
            "databases",
            "Declared databases, roles and schemas",
            view.entities((NodeKind.DATABASE,)),
            "No databases, roles or schemas are declared.",
        ),
        section(
            "storage",
            "Persistent storage and NAS declarations",
            view.entities((NodeKind.STORAGE,)),
            "No storage or NAS relationship is declared.",
        ),
        section(
            "data-access",
            "Application data access, storage consumers and ownership",
            view.relationships(
                (
                    EdgeKind.READS_DATABASE,
                    EdgeKind.USES_STORAGE,
                    EdgeKind.OWNED_BY,
                    EdgeKind.HOSTED_BY,
                ),
                data_ids,
            ),
            "No application-to-data/storage relationships are declared.",
        ),
        section(
            "recovery",
            "Encoded backup and recovery references",
            tuple(
                row
                for node in sorted(snapshot.graph.nodes, key=lambda node: node.id)
                if node.id in data_ids
                for row in view.entity_rows(
                    node, ("backup", "recovery", "rollback", "verification")
                )[1:]
            ),
            "Backup and recovery expectations are not encoded for declared "
            "data/storage entities; procedures cannot be fabricated.",
        ),
    )
    return view.document(
        DocumentKind.DATA, "Data & Storage Architecture", sections, config
    )
