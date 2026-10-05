"""One stable entry per included repository, with explicit upstream/downstream edges."""

from architecture_docs.declarations import NodeKind
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
    for repository in sorted(snapshot.graph.nodes, key=lambda node: node.id):
        if repository.kind != NodeKind.REPOSITORY or not repository.declared:
            continue
        members = tuple(
            node
            for node in sorted(snapshot.graph.nodes, key=lambda node: node.id)
            if node.repository == repository.repository
        )
        identities = frozenset(node.id for node in members)
        rows = tuple(
            row
            for node in members
            for row in view.entity_rows(
                node, () if node.kind == NodeKind.SECRET_REFERENCE else None
            )
        )
        if not any(prop.name == "purpose" for prop in repository.properties):
            rows += (
                gap(
                    f"purpose:{repository.id}",
                    "Repository purpose is not declared.",
                    (repository.id,),
                ),
            )
        rows += view.relationships(entities=identities)
        entries.append(Section(repository.id, repository.repository, rows))
    if not entries:
        entries.append(
            section(
                "included-repositories",
                "Included repositories",
                (),
                "No included repository facts are available; unavailable "
                "sources cannot be cataloged as empty repositories.",
            )
        )
    referenced = tuple(
        row
        for node in sorted(snapshot.graph.nodes, key=lambda node: node.id)
        if node.kind == NodeKind.REPOSITORY and not node.declared
        for row in view.entity_rows(node)
    )
    if referenced:
        entries.append(
            Section(
                "referenced-repositories",
                "Referenced repositories without collected declarations",
                referenced,
            )
        )
    return view.document(
        DocumentKind.CATALOG, "Repository & Dependency Catalog", tuple(entries), config
    )
