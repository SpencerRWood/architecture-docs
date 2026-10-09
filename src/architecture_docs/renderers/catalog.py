"""One stable entry per included repository, with explicit upstream/downstream edges."""

from architecture_docs.declarations import NodeKind
from architecture_docs.reconciliation import Snapshot
from architecture_docs.renderers.architecture import coverage, dependencies
from architecture_docs.renderers.artifacts import (
    DEFAULT_CONFIG,
    Document,
    DocumentKind,
    RenderConfig,
    Row,
    Section,
)
from architecture_docs.renderers.views import View, freshness, gap, section, sources


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
        purposes = tuple(
            candidate.value
            for prop in repository.properties
            if prop.name in {"purpose", "component.description"}
            for candidate in prop.candidates
        )
        dependency_targets = tuple(
            sorted(
                {
                    view.nodes[edge.target].repository
                    for edge in snapshot.graph.edges
                    if edge.source in identities
                    and view.nodes[edge.target].repository != repository.repository
                }
            )
        )
        rows = (
            Row(
                f"repository-summary:{repository.id}",
                "Repository summary",
                "Purpose: "
                + (", ".join(purposes) or "not declared")
                + f"; {len(members)} cataloged entities; cross-repository targets: "
                + (", ".join(dependency_targets) or "none evidenced")
                + ".",
                freshness(repository.evidence),
                (repository.id,),
                sources(repository.evidence),
            ),
            *(
                row
                for node in members
                for row in view.entity_rows(
                    node, () if node.kind == NodeKind.SECRET_REFERENCE else None
                )
            ),
        )
        if not any(
            prop.name in {"purpose", "component.description"}
            for prop in repository.properties
        ):
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
        DocumentKind.CATALOG,
        "Repository & Dependency Catalog",
        (*entries, dependencies(view), coverage(view)),
        config,
    )
