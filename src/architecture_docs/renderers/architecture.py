"""Classification, solution and environment views of the normalized graph."""

import html

from architecture_docs.architecture_graph import archived, preferred
from architecture_docs.classification import GROUPS
from architecture_docs.declarations import EdgeKind, NodeKind
from architecture_docs.graph import Edge, Node
from architecture_docs.renderers.artifacts import Row, Section, digest
from architecture_docs.renderers.views import View, gap, sources


def active(view: View) -> tuple[Node, ...]:
    return tuple(
        node
        for node in view.snapshot.graph.manifest(NodeKind.REPOSITORY)
        if node.declared and not archived(node)
    )


def diagram(
    view: View, identities: frozenset[str], edges: tuple[Edge, ...]
) -> str | None:
    if not identities:
        return None
    aliases = {
        identity: f"n{index}" for index, identity in enumerate(sorted(identities))
    }
    lines = ["flowchart LR"]
    for identity in sorted(identities):
        node = view.nodes[identity]
        label = html.escape(node.repository + " / " + node.name, quote=True)
        lines.append(f'  {aliases[identity]}["{label}"]')
    for edge in edges:
        if edge.source in aliases and edge.target in aliases:
            state = view.relationship(edge).state
            label = html.escape(edge.kind.value + " (" + state + ")", quote=True)
            lines.append(
                f'  {aliases[edge.source]} -->|"{label}"| {aliases[edge.target]}'
            )
    return "\n".join(lines)


def classifications(view: View) -> tuple[Section, ...]:
    repositories = active(view)
    sections = []
    for group in GROUPS:
        members = tuple(
            node
            for node in repositories
            if preferred(node, "classification.group") == group
        )
        rows = tuple(
            row
            for node in members
            for row in view.entity_rows(node, ("classification", "component"))
        )
        sections.append(
            Section(
                f"group:{digest(group)}",
                group,
                rows
                or (
                    gap(
                        f"group:{digest(group)}",
                        "No approved classified components are evidenced "
                        "in this group.",
                    ),
                ),
            )
        )
    unknown = tuple(
        node for node in repositories if preferred(node, "classification.group") is None
    )
    if unknown:
        sections.append(
            Section(
                "unclassified",
                "Unclassified components",
                tuple(row for node in unknown for row in view.entity_rows(node)),
            )
        )
    for field in ("domain", "capability"):
        values = sorted(
            {
                value
                for node in repositories
                if (value := preferred(node, f"classification.{field}"))
            }
        )
        for value in values:
            rows = tuple(
                row
                for node in repositories
                if preferred(node, f"classification.{field}") == value
                for row in view.entity_rows(node, ("classification", "component"))
            )
            sections.append(
                Section(f"{field}:{value}", f"{field.title()}: {value}", rows)
            )
    return tuple(sections)


def solutions(view: View) -> tuple[Section, ...]:
    sections = []
    repositories = active(view)
    names = sorted(
        {
            value
            for node in repositories
            if (value := preferred(node, "classification.solution"))
        }
    )
    for name in names:
        members = {
            node.repository
            for node in repositories
            if preferred(node, "classification.solution") == name
        }
        identities = frozenset(
            node.id for node in view.snapshot.graph.nodes if node.repository in members
        )
        edges = tuple(
            edge
            for edge in view.snapshot.graph.edges
            if edge.source in identities
            and edge.target in identities
            and edge.kind
            in {
                EdgeKind.DEPENDS_ON,
                EdgeKind.ORCHESTRATES,
                EdgeKind.NOTIFIES,
                EdgeKind.READS_DATABASE,
            }
        )
        rows = tuple(
            row
            for node in repositories
            if node.repository in members
            for row in view.entity_rows(node, ("classification", "component"))
        )
        rows += tuple(view.relationship(edge) for edge in edges)
        if not edges:
            rows += (
                gap(
                    f"solution:{name}:flow",
                    "Solution membership alone does not establish data flow; "
                    "relationships are unresolved.",
                ),
            )
        sections.append(
            Section(
                f"solution:{name}",
                f"Reference solution: {name}",
                rows,
                diagram(view, identities, edges),
            )
        )
    return tuple(sections)


def dependencies(view: View) -> Section:
    edges = tuple(
        edge
        for edge in view.snapshot.graph.edges
        if edge.kind in {EdgeKind.DEPENDS_ON, EdgeKind.CONSUMES_WORKFLOW}
    )
    identities = frozenset(
        identity for edge in edges for identity in (edge.source, edge.target)
    )
    return Section(
        "component-dependencies",
        "Component dependency diagram",
        tuple(view.relationship(edge) for edge in edges)
        or (gap("component-dependencies", "No component dependencies are evidenced."),),
        diagram(view, identities, edges),
    )


def environments(view: View) -> tuple[Section, ...]:
    sections = []
    for environment in view.snapshot.graph.manifest(NodeKind.ENVIRONMENT):
        edges = tuple(
            edge
            for edge in view.snapshot.graph.edges
            if (
                edge.target == environment.id
                and edge.kind in {EdgeKind.DEPLOYS_TO, EdgeKind.HOSTED_BY}
            )
            or (edge.source == environment.id and edge.kind == EdgeKind.HOSTED_BY)
        )
        identities = frozenset(
            {
                environment.id,
                *(edge.source for edge in edges),
                *(edge.target for edge in edges),
            }
        )
        rows = view.entity_rows(environment)
        for edge in edges:
            if edge.source == environment.id and edge.kind == EdgeKind.HOSTED_BY:
                rows += (
                    view.relationship(edge),
                    *view.entity_rows(view.nodes[edge.target]),
                )
                continue
            node = view.nodes[edge.source]
            rows += (
                view.relationship(edge),
                *view.entity_rows(node, ("deployment", "runtime"))[1:],
            )
            if not preferred(node, f"deployment.deployed_revision:{environment.name}"):
                rows += (
                    gap(
                        f"deployment:{edge.id}",
                        "Configured target only; actual deployment and "
                        "deployed version are unverified.",
                        (node.id,),
                    ),
                )
            rows += (
                gap(
                    f"health:{edge.id}",
                    "Runtime health requires independent verified application "
                    "evidence; deployment success alone is insufficient.",
                    (node.id,),
                ),
            )
        sections.append(
            Section(
                f"environment:{environment.id}",
                f"Environment topology: {environment.repository} / {environment.name}",
                rows,
                diagram(view, identities, edges),
            )
        )
    return tuple(sections)


def coverage(view: View) -> Section:
    rows = []
    for node in active(view):
        for field in ("domain", "capability", "kind"):
            prop = next(
                (
                    prop
                    for prop in node.properties
                    if prop.name == f"classification.{field}"
                ),
                None,
            )
            if (
                prop is None
                or prop.state != "agreed"
                or any(
                    item.verification == "stale"
                    for candidate in prop.candidates
                    for item in candidate.evidence
                )
            ):
                rows.append(
                    Row(
                        f"metadata-gap:{node.id}:{field}",
                        node.repository,
                        f"Missing, conflicting or stale architecture metadata: {field}",
                        "gap",
                        (node.id,),
                        sources(node.evidence),
                    )
                )
    for edge in view.snapshot.graph.edges:
        row = view.relationship(edge)
        if row.state.startswith(("unresolved", "inferred")) or "stale" in row.state:
            rows.append(row)
    rows.extend(view.diagnostics(frozenset(view.nodes)))
    return Section(
        "architecture-coverage",
        "Architecture metadata and relationship coverage",
        tuple(rows)
        or (
            Row(
                "architecture-coverage:complete",
                "Coverage",
                "No missing metadata or unresolved relationship evidence."
                if active(view)
                else "No approved component evidence is available to assess coverage.",
                "verified" if active(view) else "gap",
            ),
        ),
    )
