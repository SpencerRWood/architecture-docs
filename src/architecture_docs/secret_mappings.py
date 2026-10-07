"""Deterministic consumer-to-location mapping over normalized snapshot evidence."""

import json
from dataclasses import dataclass, replace

from architecture_docs.collectors.infisical import Location
from architecture_docs.declarations import EdgeKind, NodeKind
from architecture_docs.graph import Evidence, Node, ordered
from architecture_docs.infisical_scope import Binding
from architecture_docs.reconciliation import Snapshot
from architecture_docs.secret_consumers import declared_targets, workflow_mappings


@dataclass(frozen=True)
class SecretMapping:
    repository: str
    consumer: str
    consumer_name: str
    locations: tuple[tuple[Location, Evidence], ...]
    injection: str
    required: str
    owner: str
    status: str
    evidence: tuple[Evidence, ...]
    provider: str = "infisical"
    target: tuple[str, ...] = ()
    via: str = ""


def property_value(node: Node, name: str) -> str:
    prop = next((p for p in node.properties if p.name == name), None)
    return prop.preferred if prop and prop.state == "agreed" and prop.preferred else ""


def mappings(  # noqa: PLR0912, PLR0915 - explicit evidence precedence and usage branches
    snapshot: Snapshot,
) -> tuple[tuple[SecretMapping, ...], tuple[tuple[Location, Evidence], ...]]:
    locations = tuple(
        (Location(**json.loads(e.observation.value)), e)
        for e in snapshot.evidence
        if e.observation.key == "infisical.location"
    )
    bindings = tuple(
        (Binding(**b), e)
        for e in snapshot.evidence
        if e.observation.key == "infisical.contract"
        for b in json.loads(e.observation.value)["bindings"]
    )
    nodes = {node.id: node for node in snapshot.graph.nodes}
    result: list[SecretMapping] = []
    used: set[Location] = set()
    for node in snapshot.graph.manifest(NodeKind.SECRET_REFERENCE):
        explicit = tuple(
            (b, e)
            for b, e in bindings
            if b.repository == node.repository and b.consumer == node.name
        )
        associations = tuple(
            (b, e)
            for b, e in bindings
            if b.repository == node.repository and not b.consumer
        )
        declarations = tuple(
            property_value(node, field) for field in ("project", "environment", "path")
        )
        candidates: tuple[tuple[Location, Evidence], ...] = ()
        extra: tuple[Evidence, ...] = ()
        targets: set[tuple[str, ...]] = set()
        conflicting = any(
            p.state == "ambiguous"
            for p in node.properties
            if p.name in {"project", "environment", "path", "infisical_key"}
        )
        if explicit:
            targets = {(b.project, b.environment, b.path, b.key) for b, _ in explicit}
            candidates = tuple(
                (loc, e)
                for loc, e in locations
                if (loc.project, loc.environment, loc.path, loc.key) in targets
            )
            extra = tuple(e for _, e in explicit)
            conflicting = len(targets) != 1
        elif any(
            p.name in {"project", "environment", "path", "infisical_key"}
            for p in node.properties
        ):
            alias_conflict = any(
                p.name == "infisical_key" and p.state != "agreed"
                for p in node.properties
            )
            if all(declarations) and not conflicting and not alias_conflict:
                targets = {
                    (*declarations, property_value(node, "infisical_key") or node.name)
                }
                candidates = tuple(
                    (loc, e)
                    for loc, e in locations
                    if (
                        loc.project == declarations[0]
                        or loc.project_name == declarations[0]
                    )
                    and (loc.environment, loc.path) == declarations[1:]
                    and loc.key == (property_value(node, "infisical_key") or node.name)
                )
        else:
            declared, declared_evidence = declared_targets(snapshot, node.repository)
            path_targets = declared or {
                (b.project, b.environment, b.path) for b, _ in associations
            }
            if not declared and not associations:
                folder = "/" + node.repository.split("/")[-1]
                scopes = {
                    (s["project"], s["environment"], s["path"])
                    for e in snapshot.evidence
                    if e.observation.key == "infisical.contract"
                    for s in json.loads(e.observation.value)["scopes"]
                    if s["path"] == folder
                }
                scopes.update(
                    (loc.project, loc.environment, loc.path)
                    for loc, _ in locations
                    if loc.path == folder
                )
                path_targets = scopes if len(scopes) == 1 else set()
            targets = {(*target, node.name) for target in path_targets}
            candidates = tuple(
                (loc, e)
                for loc, e in locations
                if any(
                    p in (loc.project, loc.project_name)
                    and (loc.environment, loc.path) == (env, path)
                    for p, env, path in path_targets
                )
                and loc.key == node.name
            )
            extra = declared_evidence or tuple(e for _, e in associations)
            conflicting = len(path_targets) > 1
        identities = {loc for loc, _ in candidates}
        status = (
            "ambiguous"
            if conflicting or len(identities) > 1
            else "mapped"
            if candidates
            else "location-declared"
            if targets
            else "unresolved"
        )
        if status == "location-declared":
            status = (
                "missing-key"
                if any(
                    (
                        project == s["project"]
                        or any(
                            loc.project == s["project"] and loc.project_name == project
                            for loc, _ in locations
                        )
                    )
                    and (environment, path) == (s["environment"], s["path"])
                    for project, environment, path, _ in targets
                    for e in snapshot.evidence
                    if e.observation.key == "infisical.contract"
                    for s in json.loads(e.observation.value)["scopes"]
                )
                else status
            )
        evidence = ordered([*node.evidence, *extra, *(e for _, e in candidates)])
        if status in {"mapped", "location-declared", "missing-key"} and any(
            e.verification == "stale" for e in evidence
        ):
            status = "stale"
        consumers = tuple(
            nodes[edge.source].name
            for edge in snapshot.graph.edges
            if edge.kind == EdgeKind.CONSUMES_SECRET and edge.target == node.id
        ) or (node.repository,)
        selected = explicit or (associations if candidates else ())
        metadata = []
        for field in ("injection", "required", "owner"):
            values = {getattr(b, field) for b, _ in selected if getattr(b, field)}
            metadata.append(
                property_value(node, field)
                or (next(iter(values)) if len(values) == 1 else "")
            )
        owner = property_value(node, f"relationship:{EdgeKind.OWNED_BY}")
        if not metadata[2] and owner in nodes:
            metadata[2] = nodes[owner].name
        base = candidates, evidence, status, targets
        for consumer in sorted(set(consumers)):
            candidates, evidence, status, targets = base
            if (
                consumer.startswith(".github/workflows/")
                and not explicit
                and property_value(node, "injection") != "runtime"
            ):
                result.extend(workflow_mappings(snapshot, node, consumer))
                continue
            service_targets, service_evidence = declared_targets(
                snapshot, node.repository, consumer
            )
            if (
                service_targets
                and not explicit
                and not any(
                    p.name in {"project", "environment", "path", "infisical_key"}
                    for p in node.properties
                )
            ):
                targets = {(*target, node.name) for target in service_targets}
                candidates = tuple(
                    (loc, e)
                    for loc, e in locations
                    if any(
                        p in (loc.project, loc.project_name)
                        and (loc.environment, loc.path, loc.key) == (env, path, key)
                        for p, env, path, key in targets
                    )
                )
                evidence = ordered(
                    [*node.evidence, *service_evidence, *(e for _, e in candidates)]
                )
                status = (
                    "ambiguous"
                    if len(targets) > 1 or len({loc for loc, _ in candidates}) > 1
                    else "mapped"
                    if candidates
                    else "location-declared"
                )
                if status != "ambiguous" and any(
                    e.verification == "stale" for e in evidence
                ):
                    status = "stale"
            used.update(loc for loc, _ in candidates)
            injection = (
                "github-actions"
                if consumer.startswith(".github/workflows/")
                else metadata[0]
            )
            result.append(
                SecretMapping(
                    node.repository,
                    consumer,
                    node.name,
                    tuple(sorted(candidates, key=lambda pair: pair[0])),
                    injection,
                    metadata[1],
                    metadata[2],
                    status,
                    evidence,
                    "infisical" if targets or candidates else "unknown",
                    next(iter(targets)) if len(targets) == 1 else (),
                )
            )
    for e in snapshot.evidence:
        if e.observation.key != "workflow.input_ref":
            continue
        provenance = e.observation.provenance
        name = "input:" + json.loads(e.observation.value)["name"]
        node = Node(
            "input-reference",
            NodeKind.SECRET_REFERENCE,
            provenance.repository,
            name,
            False,
            (),
            (e,),
        )
        result.extend(
            entry
            for entry in workflow_mappings(snapshot, node, provenance.source)
            if entry.status in {"resolved", "stale"}
        )
    merged: dict[tuple[str, str, str, str, str, tuple[str, ...]], SecretMapping] = {}
    for entry in result:
        key = (
            entry.repository,
            entry.consumer,
            entry.consumer_name,
            entry.provider,
            entry.status,
            entry.target,
        )
        previous = merged.get(key)
        if previous:
            combined = replace(
                entry,
                evidence=ordered([*previous.evidence, *entry.evidence]),
                via="; ".join(sorted(set(filter(None, (previous.via, entry.via))))),
            )
        merged[key] = combined if previous else entry
    return tuple(
        sorted(
            merged.values(), key=lambda m: (m.repository, m.consumer_name, m.consumer)
        )
    ), tuple(
        sorted(
            ((loc, e) for loc, e in locations if loc not in used),
            key=lambda pair: pair[0],
        )
    )
