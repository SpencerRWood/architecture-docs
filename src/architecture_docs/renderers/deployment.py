"""Delivery stage contracts are declarations, not attestations that stages ran."""

from architecture_docs.declarations import EdgeKind, NodeKind
from architecture_docs.reconciliation import Snapshot
from architecture_docs.renderers.architecture import environments
from architecture_docs.renderers.artifacts import (
    DEFAULT_CONFIG,
    Document,
    DocumentKind,
    RenderConfig,
)
from architecture_docs.renderers.views import View, section

STAGES = (
    ("validation", "Validation contracts", ("validation", "checks")),
    ("candidate", "Candidate artifacts", ("candidate_artifact", "artifact", "build")),
    (
        "runtime-gate",
        "Runtime gates",
        (
            "runtime_gate",
            "runtime_validation",
            "release.runtime_validation",
            "dagster.runtime_validation",
            "container.runtime_validation",
        ),
    ),
    (
        "release",
        "Release publication",
        ("release", "publish", "semantic_release"),
    ),
    ("promotion", "Promotion", ("promotion", "promotion_workflow")),
    ("deployment", "Deployment paths", ("deployment", "deployment_path")),
    (
        "rollback",
        "Rollback and known-good recovery references",
        ("rollback", "recovery"),
    ),
    ("verification", "Verification contracts", ("verification",)),
)


def render(snapshot: Snapshot, config: RenderConfig = DEFAULT_CONFIG) -> Document:
    view = View(snapshot)
    stages = []
    for identity, title, fields in STAGES:
        rows = view.properties(fields)
        if identity == "release":
            rows += tuple(
                row
                for node in snapshot.graph.nodes
                if node.kind == NodeKind.RELEASE_CONTRACT
                for row in view.entity_rows(node, ("workflow",))[1:]
            )
        stages.append(
            section(
                identity,
                title,
                rows,
                f"{title} are not evidenced in the normalized snapshot.",
            )
        )
    sections = (*tuple(stages), *environments(view))
    sections += (
        section(
            "release-contracts",
            "Declared release contracts",
            view.entities((NodeKind.RELEASE_CONTRACT,)),
            "No release contract is declared.",
        ),
        section(
            "delivery-relationships",
            "Deployment ownership, targets and workflow consumption",
            view.relationships(
                (
                    EdgeKind.DEPLOYMENT_OWNER,
                    EdgeKind.DEPLOYS_TO,
                    EdgeKind.HOSTED_BY,
                    EdgeKind.CONSUMES_WORKFLOW,
                )
            ),
            "No deployment or shared workflow relationships are declared.",
        ),
    )
    return view.document(
        DocumentKind.DEPLOYMENT, "Deployment & Release Architecture", sections, config
    )
