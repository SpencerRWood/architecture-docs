"""Ordered repository-contract references; never synthesize executable commands."""

from collections import defaultdict

from architecture_docs.declarations import NodeKind, Phase, RunbookKind
from architecture_docs.graph import Evidence, Node
from architecture_docs.reconciliation import Snapshot
from architecture_docs.renderers.artifacts import (
    DEFAULT_CONFIG,
    Document,
    DocumentKind,
    RenderConfig,
    Row,
    Section,
    digest,
)
from architecture_docs.renderers.views import View, freshness, gap, sources

TITLES = {
    RunbookKind.DEPLOYMENT: "Deployment / Redeployment",
    RunbookKind.ROLLBACK: "Rollback / Known-good Recovery",
    RunbookKind.HOST: "Linux Host Rebuild / Recovery",
    RunbookKind.SECRETS: "Secret-reference Management",
    RunbookKind.DATABASE: "Database Provisioning / Onboarding",
    RunbookKind.RELEASE: "Release / Promotion Failure Troubleshooting",
    RunbookKind.RESTORATION: "Service Restoration",
    RunbookKind.DAGSTER: "Dagster Operations",
}

SUPPORT_FIELDS = {
    RunbookKind.DEPLOYMENT: ("deployment", "deployment_path", "promotion_workflow"),
    RunbookKind.ROLLBACK: ("rollback", "recovery"),
    RunbookKind.HOST: ("recovery",),
    RunbookKind.SECRETS: ("injection",),
    RunbookKind.DATABASE: ("backup", "recovery"),
    RunbookKind.RELEASE: (
        "workflow",
        "script",
        "reference",
        "release",
        "publish",
        "promotion_workflow",
    ),
    RunbookKind.RESTORATION: ("recovery", "verification"),
    RunbookKind.DAGSTER: ("code_location", "dagster"),
}


SOURCE_HINTS = {
    RunbookKind.DEPLOYMENT: ("deploy", "promot"),
    RunbookKind.ROLLBACK: ("rollback", "recover", "restore"),
    RunbookKind.HOST: ("host", "ansible", "provision"),
    RunbookKind.SECRETS: ("secret", "credential", "infisical"),
    RunbookKind.DATABASE: ("database", "postgres", "backup", "dbt"),
    RunbookKind.RELEASE: ("release", "promot", "deploy", "rollback", "verif"),
    RunbookKind.RESTORATION: ("restore", "recover", "health"),
    RunbookKind.DAGSTER: ("dagster", "code_location"),
}


def candidate_sources(
    view: View, kind: RunbookKind, procedures: tuple[Node, ...]
) -> tuple[Row, ...]:
    """Path hints discover existing sources; they never establish applicability."""
    declared_repositories = {node.repository for node in procedures}
    current = set(view.snapshot.collection.observations)
    coverage = {
        (item.repository, item.source, item.collector, item.revision)
        for item in view.snapshot.collection.coverage
    }
    groups: dict[str, list[Evidence]] = defaultdict(list)
    for item in view.snapshot.evidence:
        observation = item.observation
        provenance = observation.provenance
        if (
            item.verification == "verified"
            and observation in current
            and observation.key == "source.kind"
            and (
                (
                    observation.collector == "executable"
                    and provenance.source.startswith((".github/workflows/", "scripts/"))
                )
                or (
                    observation.collector == "configuration"
                    and any(
                        peer.observation.provenance == provenance
                        and peer.observation.key.startswith("ansible.")
                        for peer in view.snapshot.evidence
                    )
                )
            )
            and provenance.repository not in declared_repositories
            and provenance.revision is not None
            and (
                provenance.repository,
                provenance.source,
                observation.collector,
                provenance.revision,
            )
            in coverage
            and any(hint in provenance.source.lower() for hint in SOURCE_HINTS[kind])
        ):
            groups[provenance.repository].append(item)
    return tuple(
        Row(
            "candidate-sources:"
            + digest((repository, item.observation.provenance.source)),
            f"{repository}/{item.observation.provenance.source}",
            "Collected source; applicability to this runbook is not declared.",
            "gap",
            tuple(
                node.id
                for node in view.snapshot.graph.nodes
                if node.kind == NodeKind.REPOSITORY and node.repository == repository
            ),
            sources((item,)),
        )
        for repository, evidence in sorted(groups.items())
        for item in evidence
    )


def operator_asset(
    value: str, repository: str, *, explicit: bool = False
) -> str | None:
    """Recognize operator source paths, never generic action or package identifiers."""
    normalized = value.removeprefix("./")
    path = normalized.split("@", 1)[0]
    if path.startswith((".github/workflows/", "scripts/", "ansible/", "playbooks/")):
        return f"{repository}/{normalized}"
    parts = path.split("/", 2)
    if (
        len(parts) == 3
        and parts[0] == repository.split("/", 1)[0]
        and parts[2].startswith(".github/workflows/")
    ):
        return normalized
    return normalized if explicit else None


def supporting_contracts(view: View, kind: RunbookKind) -> tuple[Row, ...]:
    """Point to typed contracts, never classify a script by its filename."""
    rows = []
    for node in sorted(view.snapshot.graph.nodes, key=lambda node: node.id):
        if node.kind == NodeKind.SECRET_REFERENCE:
            continue
        for row in view.entity_rows(node, SUPPORT_FIELDS[kind])[1:]:
            explicit = node.kind == NodeKind.PROCEDURE and any(
                item.observation.key == "architecture.node" for item in node.evidence
            )
            asset = operator_asset(row.value, node.repository, explicit=explicit)
            if asset is None:
                continue
            declared = any(
                item.observation.key == "architecture.node" for item in node.evidence
            )
            if not declared and not any(
                hint in asset.lower() for hint in SOURCE_HINTS[kind]
            ):
                continue
            rows.append(
                Row(
                    f"support:{kind}:{row.id}",
                    asset,
                    "Supporting reference; it does not establish "
                    "an executable procedure.",
                    row.state,
                    row.entities,
                    row.sources,
                )
            )
    # Generic executable inventory is kept in the catalog, not every runbook.
    return tuple(rows)


def aggregate_support(kind: RunbookKind, rows: tuple[Row, ...]) -> tuple[Row, ...]:
    groups: dict[str, list[Row]] = defaultdict(list)
    for row in rows:
        groups[row.label].append(row)
    return tuple(
        Row(
            f"candidate-sources:{digest((kind, asset))}",
            asset,
            "Consumer/source repositories: "
            + ", ".join(
                sorted({source.repository for row in grouped for source in row.sources})
            )
            + ". Discovery alone does not establish applicability or executable steps.",
            "; ".join(sorted({row.state for row in grouped})),
            tuple(sorted({entity for row in grouped for entity in row.entities})),
            tuple(sorted({source for row in grouped for source in row.sources})),
        )
        for asset, grouped in sorted(groups.items())
    )


def agreed(node: Node, field: str) -> str | None:
    prop = next((prop for prop in node.properties if prop.name == field), None)
    if (
        prop is None
        or prop.state != "agreed"
        or any(
            freshness(candidate.evidence) != "verified" for candidate in prop.candidates
        )
    ):
        return None
    return prop.preferred


def grounding(
    snapshot: Snapshot, node: Node, target: str, kind: str
) -> tuple[Evidence, ...]:
    """Require a completely parsed approved source at the declaration's revision."""
    revisions = {item.observation.provenance.revision for item in node.evidence}
    collector = "executable" if kind in {"script", "workflow"} else None
    return tuple(
        item
        for item in snapshot.evidence
        if item.verification == "verified"
        and item.observation.key == "source.kind"
        and item.observation.provenance.repository == node.repository
        and item.observation.provenance.source == target
        and item.observation.provenance.revision is not None
        and item.observation.provenance.revision in revisions
        and (collector is None or item.observation.collector == collector)
    )


def step_rows(snapshot: Snapshot, node: Node) -> tuple[Row, ...]:
    fields = ("runbook", "phase", "order")
    if (
        any(agreed(node, field) is None for field in fields)
        or freshness(node.evidence) != "verified"
    ):
        return (
            gap(
                node.id,
                "Procedure has stale, conflicting or incomplete step metadata; "
                "execution withheld.",
                (node.id,),
            ),
        )
    targets = [
        (field, agreed(node, field))
        for field in ("script", "workflow", "reference")
        if any(prop.name == field for prop in node.properties)
    ]
    target = targets[0][1] if len(targets) == 1 else None
    if target is None:
        return (
            gap(
                node.id,
                "Procedure source reference is missing or conflicting; "
                "execution withheld.",
                (node.id,),
            ),
        )
    kind = targets[0][0]
    evidence = grounding(snapshot, node, target, kind)
    if not evidence:
        return (
            gap(
                node.id,
                "Referenced source is unavailable, unapproved, stale or not verified "
                "at the procedure revision; execution withheld.",
                (node.id,),
            ),
        )
    return (
        Row(
            digest((node.id, kind, target)),
            f"{agreed(node, 'order')}. {node.name}",
            f"Follow repository {kind} contract: {target}. "
            "Use its declared invocation, inputs and conditions; "
            "this reference does not supply command arguments.",
            "grounded reference",
            (node.id,),
            sources(node.evidence + evidence),
        ),
    )


def render_all(
    snapshot: Snapshot, config: RenderConfig = DEFAULT_CONFIG
) -> tuple[Document, ...]:
    view = View(snapshot)
    documents = []
    for kind in RunbookKind:
        # Keep ambiguous candidates in every affected family for diagnosis.
        nodes = tuple(
            node
            for node in snapshot.graph.manifest(NodeKind.PROCEDURE)
            if any(
                prop.name == "runbook"
                and any(candidate.value == kind for candidate in prop.candidates)
                for prop in node.properties
            )
        )
        sections = []
        for phase in Phase:
            selected = tuple(
                node
                for node in nodes
                if any(
                    prop.name == "phase"
                    and any(candidate.value == phase for candidate in prop.candidates)
                    for prop in node.properties
                )
            )
            ordered = sorted(
                selected,
                key=lambda node: (
                    node.repository,
                    int(agreed(node, "order") or "99999"),
                    node.id,
                ),
            )
            rows = tuple(
                row
                for node in ordered
                for row in (
                    (
                        gap(
                            node.id,
                            "Duplicate step order; execution withheld.",
                            (node.id,),
                        ),
                    )
                    if sum(
                        peer.repository == node.repository
                        and agreed(peer, "order") == agreed(node, "order")
                        for peer in selected
                    )
                    > 1
                    else step_rows(snapshot, node)
                )
            )
            for repository in sorted({node.repository for node in nodes}):
                if not any(node.repository == repository for node in selected):
                    rows += (
                        gap(
                            f"{kind}:{phase}:{repository}",
                            f"{repository}: no evidenced {phase.value} "
                            "contract is declared.",
                        ),
                    )
            rows = rows or (
                gap(
                    f"{kind}:{phase}",
                    f"No evidenced {phase.value} contract is declared "
                    "for this runbook.",
                ),
            )
            sections.append(
                Section(phase.value, phase.value.replace("-", " / ").title(), rows)
            )
        if nodes:
            sections.append(
                Section(
                    "procedure-evidence",
                    "Procedure declarations",
                    tuple(row for node in nodes for row in view.entity_rows(node)),
                )
            )
        support = aggregate_support(
            kind,
            supporting_contracts(view, kind) + candidate_sources(view, kind, nodes),
        )
        if support:
            sections.append(
                Section(
                    "supporting-contracts",
                    "Supporting contracts and missing procedures",
                    (
                        Row(
                            f"procedure-requirements:{kind}",
                            "Required procedure contract",
                            f"Explicit procedure requires runbook={kind}, phase, order "
                            "and one approved script/workflow/reference "
                            "at the same revision. "
                            "Declare prerequisites, steps, verification and recovery. "
                            "Execution remains withheld for candidates; "
                            "applicability to this runbook is not declared "
                            "by discovery.",
                            "requirement",
                        ),
                        *support,
                    ),
                )
            )
        documents.append(
            view.document(
                DocumentKind(f"runbook-{kind}"),
                f"Runbook: {TITLES[kind]}",
                tuple(sections),
                config,
            )
        )
    return tuple(documents)
