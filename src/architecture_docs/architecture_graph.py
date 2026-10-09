"""Enrich the common graph from bounded observations, preserving all evidence."""

import json
import re
from dataclasses import replace

from architecture_docs.classification import CAPABILITIES, REFERENCE_COMPONENTS, role
from architecture_docs.declarations import EdgeKind, NodeKind
from architecture_docs.graph import Builder, Edge, Evidence, Gap, Graph, Node, node_id


def preferred(node: Node, field: str) -> str | None:
    return next(
        (prop.preferred for prop in node.properties if prop.name == field), None
    )


def archived(node: Node) -> bool:
    return preferred(node, "repository.archived") == "true"


def relationship_state(edge: Edge, graph: Graph) -> str:
    """Confidence is independent of whether a source was read successfully."""
    nodes = {node.id: node for node in graph.nodes}
    if not nodes[edge.source].declared or not nodes[edge.target].declared:
        return "unresolved"
    declarations = [
        item
        for item in edge.evidence
        if item.observation.key == "architecture.relationship"
    ]
    if any(
        json.loads(item.observation.value)["state"] == "inferred"
        for item in declarations
    ) or (
        nodes[edge.target].kind == NodeKind.REPOSITORY
        and any(item.observation.key == "package.dependency" for item in edge.evidence)
    ):
        return "inferred"
    if any(
        item.observation.key == "deployment.attested"
        and item.verification == "verified"
        for item in edge.evidence
    ):
        return "verified"
    # Static verification requires corroboration by independent source families,
    # at current pinned revisions. It never attests runtime execution or health.
    families = {
        family(item.observation.provenance.source)
        for item in edge.evidence
        if item.verification == "verified" and item.observation.provenance.revision
    }
    return "verified" if len(families) > 1 else "declared"


def family(path: str) -> str:
    if path.startswith(".github/"):
        return "github-actions"
    if path.endswith((".lock", "pyproject.toml")):
        return "python-packaging"
    if path.startswith("models/") or path.endswith("manifest.json"):
        return "dbt"
    if "workspace" in path or path.endswith(".py"):
        return "dagster"
    return "configuration"


def fact(builder: Builder, root: str, item: Evidence) -> None:
    observation = item.observation
    key, value = observation.key, observation.value
    if key.startswith(("classification.", "component.")):
        builder.property(root, key, value, item)
    elif key == "architecture.relationship":
        data = json.loads(value)
        endpoints = []
        for side in ("source", "target"):
            ref = data[side]
            endpoints.append(
                builder.node(
                    NodeKind(ref["kind"]),
                    ref["name"],
                    item,
                    ref.get("repository"),
                    declared=False,
                )
            )
        builder.edge(EdgeKind(data["kind"]), endpoints[0], endpoints[1], item)
    elif key in {"data.component", "orchestration.component", "dagster.location"}:
        kind = NodeKind.SYSTEM if key == "data.component" else NodeKind.ORCHESTRATION
        component = builder.node(kind, value, item)
        builder.edge(EdgeKind.CONTAINS, root, component, item)
        if key == "dagster.location":
            builder.property(component, "code_location", value, item)
    elif key in {"environment.configured", "environment.supported"}:
        environment = builder.node(NodeKind.ENVIRONMENT, value, item)
        builder.property(environment, key, "true", item)
        builder.edge(EdgeKind.CONTAINS, root, environment, item)
    elif key == "deployment.selection":
        data = json.loads(value)
        environment = builder.node(NodeKind.ENVIRONMENT, data["environment"], item)
        service = builder.node(NodeKind.SERVICE, data["service"], item, declared=False)
        builder.edge(EdgeKind.DEPLOYS_TO, service, environment, item)
        builder.property(
            service, f"deployment.selected:{data['environment']}", "true", item
        )
    elif key == "deployment.image":
        data = json.loads(value)
        service = builder.node(NodeKind.SERVICE, data["service"], item)
        builder.property(service, "deployment.configured_image", data["image"], item)
        builder.edge(EdgeKind.CONTAINS, root, service, item)
    elif key == "deployment.attested":
        data = json.loads(value)
        service = builder.node(NodeKind.SERVICE, data["service"], item)
        environment = builder.node(NodeKind.ENVIRONMENT, data["environment"], item)
        builder.edge(EdgeKind.CONTAINS, root, service, item)
        builder.edge(EdgeKind.CONTAINS, root, environment, item)
        builder.edge(EdgeKind.DEPLOYS_TO, service, environment, item)
        builder.property(
            service,
            f"deployment.deployed_revision:{data['environment']}",
            data["revision"],
            item,
        )
        builder.property(
            service, f"runtime.health:{data['environment']}", "unavailable", item
        )


def enrich(builder: Builder, evidence: tuple[Evidence, ...]) -> Graph:  # noqa: PLR0912 - independent enrichment phases
    """Resolve package identities using collected project names, never guesses."""
    initial = builder.finish()
    names: dict[str, list[Node]] = {}
    ambiguous_packages: list[Gap] = []
    direct_dependencies = {
        (
            item.observation.provenance.repository,
            re.sub(r"[-_.]+", "-", item.observation.value.lower()),
        )
        for item in evidence
        if item.observation.key == "package.dependency"
    }
    for node in initial.manifest(NodeKind.REPOSITORY):
        if (
            node.declared
            and node.repository.rsplit("/", 1)[-1] in REFERENCE_COMPONENTS
            and not any(
                prop.name == "classification.solution" for prop in node.properties
            )
        ):
            for item in node.evidence[:1]:
                builder.property(
                    node.id,
                    "classification.solution",
                    "synthetic-website-analytics-platform",
                    item,
                )
                builder.property(
                    node.id, "classification.solution_state", "inferred", item
                )
        if name := preferred(node, "component.name"):
            names.setdefault(re.sub(r"[-_.]+", "-", name.lower()), []).append(node)
        declared_capability = preferred(node, "classification.capability")
        capability = (
            declared_capability
            if any(prop.name == "classification.capability" for prop in node.properties)
            else role(node.repository)
        )
        if capability:
            items = (
                tuple(
                    item
                    for item in node.evidence
                    if item.observation.key.startswith("classification.")
                )
                if declared_capability
                else tuple(
                    item
                    for item in node.evidence
                    if item.observation.key == "repository.identity"
                )
                or node.evidence[:1]
            )
            for item in items:
                builder.property(
                    node.id, "classification.group", CAPABILITIES[capability], item
                )
                if not declared_capability:
                    builder.property(node.id, "classification.state", "inferred", item)
    for item in evidence:
        observation = item.observation
        root = node_id(
            NodeKind.REPOSITORY,
            observation.provenance.repository,
            observation.provenance.repository,
        )
        if observation.key == "package.dependency":
            candidates = names.get(
                re.sub(r"[-_.]+", "-", observation.value.lower()), []
            )
            if len(candidates) == 1:
                builder.edge(EdgeKind.DEPENDS_ON, root, candidates[0].id, item)
                for corroboration in candidates[0].evidence:
                    if corroboration.observation.key == "component.name":
                        builder.edge(
                            EdgeKind.DEPENDS_ON, root, candidates[0].id, corroboration
                        )
            elif len(candidates) > 1:
                ambiguous_packages.append(Gap(root, "ambiguous_package_repository"))
        elif observation.key == "package.repository":
            data = json.loads(observation.value)
            target = builder.node(
                NodeKind.REPOSITORY,
                data["repository"],
                item,
                data["repository"],
                declared=False,
            )
            direct = (
                observation.provenance.repository,
                re.sub(r"[-_.]+", "-", data["name"].lower()),
            ) in direct_dependencies
            package = builder.node(NodeKind.PACKAGE, data["name"], item)
            builder.edge(EdgeKind.DEPENDS_ON, root if direct else package, target, item)
    graph = builder.finish()
    gaps = [*graph.gaps, *ambiguous_packages]
    for node in graph.manifest(NodeKind.REPOSITORY):
        if not node.declared or archived(node):
            continue
        for field in ("domain", "capability", "kind"):
            if preferred(node, f"classification.{field}") is None:
                gaps.append(Gap(node.id, f"missing_metadata_{field}"))
    gaps.extend(
        Gap(edge.id, "unresolved_relationship")
        for edge in graph.edges
        if relationship_state(edge, graph) == "unresolved"
    )
    # View diagnostics expect gap entities to refer to nodes. Keep unresolved
    # edges attached to their source; edge rows retain exact target provenance.
    edges = {edge.id: edge for edge in graph.edges}
    gaps = [
        Gap(edges[gap.entity].source, gap.reason) if gap.entity in edges else gap
        for gap in gaps
    ]
    return replace(graph, gaps=tuple(sorted(set(gaps))))
