"""Ordered repository-contract references; never synthesize executable commands."""

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
        documents.append(
            view.document(
                DocumentKind(f"runbook-{kind}"),
                f"Runbook: {TITLES[kind]}",
                tuple(sections),
                config,
            )
        )
    return tuple(documents)
