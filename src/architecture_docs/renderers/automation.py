"""Explicit orchestration, delivery, planning/reporting and event integration views."""

from architecture_docs.declarations import EdgeKind, NodeKind
from architecture_docs.reconciliation import Snapshot
from architecture_docs.renderers.artifacts import (
    DEFAULT_CONFIG,
    Document,
    DocumentKind,
    RenderConfig,
    Section,
)
from architecture_docs.renderers.views import View, section

CAPABILITIES = (
    ("dagster", "Dagster jobs and code locations"),
    ("renovate", "Renovate automation"),
    ("codex", "Codex-driven workflows"),
    ("openproject", "OpenProject interactions"),
    ("reporting", "Reporting automation"),
    ("repair", "Repair and recovery automation"),
    ("events", "Notification and event integrations"),
)


def render(snapshot: Snapshot, config: RenderConfig = DEFAULT_CONFIG) -> Document:
    view = View(snapshot)
    sections: tuple[Section, ...] = (
        section(
            "automations",
            "Declared automations, jobs and code locations",
            view.entities((NodeKind.ORCHESTRATION, NodeKind.SYSTEM, NodeKind.SERVICE)),
            "No automation declarations are available; Dagster, Renovate, "
            "Codex, OpenProject, reporting and repair capabilities "
            "require explicit evidence.",
        ),
        section(
            "release",
            "Centralized release contracts and workflow consumption",
            view.entities((NodeKind.RELEASE_CONTRACT,))
            + view.relationships((EdgeKind.CONSUMES_WORKFLOW,)),
            "No centralized release or shared workflow relationship is declared.",
        ),
        section(
            "operations",
            "Planning, reporting and repair/recovery contract inventory",
            view.entities(
                (NodeKind.PROCEDURE,),
                (
                    "script",
                    "workflow",
                    "purpose",
                    "technology",
                    "contract.kind",
                    "shell.executable",
                    "python.declaration",
                    "recovery",
                ),
            ),
            "No executable operational contract inventory is available; "
            "commands and recovery steps remain unknown.",
        ),
        section(
            "events",
            "Notification and event interfaces",
            view.entities((NodeKind.INTERFACE,))
            + view.relationships((EdgeKind.NOTIFIES, EdgeKind.EXPOSES)),
            "No notification or event interfaces are declared.",
        ),
        section(
            "orchestration",
            "Orchestration and integration dependencies",
            view.relationships(
                (EdgeKind.ORCHESTRATES, EdgeKind.DEPENDS_ON, EdgeKind.OPERATED_BY)
            ),
            "No orchestration or integration dependency is declared.",
        ),
    )
    sections += tuple(
        section(
            f"capability-{tag}",
            title,
            tuple(
                row
                for node in sorted(snapshot.graph.nodes, key=lambda node: node.id)
                if any(
                    prop.name in {"technology", "purpose"}
                    and any(candidate.value == tag for candidate in prop.candidates)
                    for prop in node.properties
                )
                for row in view.entity_rows(
                    node,
                    ("technology", "purpose", "code_location", "workflow", "recovery"),
                )
            ),
            f"{title} are not explicitly identified by normalized "
            "technology or purpose metadata.",
        )
        for tag, title in CAPABILITIES
    )
    return view.document(
        DocumentKind.AUTOMATION,
        "Automation & Orchestration Architecture",
        sections,
        config,
    )
