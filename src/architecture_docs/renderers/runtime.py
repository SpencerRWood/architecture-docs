"""Declared runtime targets, interfaces, persistence, and ownership boundaries."""

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
    sections = (
        section(
            "targets",
            "Declared environments, hosts and runtime services",
            view.entities(
                (
                    NodeKind.ENVIRONMENT,
                    NodeKind.HOST,
                    NodeKind.SERVICE,
                    NodeKind.SYSTEM,
                ),
                ("purpose", "technology", "host", "environment", "owner", "port"),
            ),
            "Environments, hosts and runtime targets are not declared.",
        ),
        section(
            "networking",
            "Interfaces, networking and reverse-proxy declarations",
            view.entities((NodeKind.INTERFACE,)),
            "No networking or reverse-proxy metadata is declared; "
            "reachability is unknown.",
        ),
        section(
            "code-locations",
            "Orchestration and Dagster code-location metadata",
            view.entities(
                (NodeKind.ORCHESTRATION,),
                ("technology", "code_location", "host", "environment", "workflow"),
            ),
            "No orchestration or code-location declarations are available.",
        ),
        section(
            "code-location-contracts",
            "Explicit code-location contracts",
            view.properties(("code_location",)),
            "Dagster code-location identifiers are not explicitly declared.",
        ),
        section(
            "network-contracts",
            "Explicit networking and reverse-proxy contracts",
            view.properties(("network", "reverse_proxy")),
            "Network and reverse-proxy responsibilities are not explicitly declared.",
        ),
        section(
            "persistence",
            "Persistent storage and shared database responsibilities",
            view.entities(
                (NodeKind.DATABASE, NodeKind.STORAGE),
                ("purpose", "technology", "role", "schema"),
            ),
            "Storage and database responsibilities are not declared.",
        ),
        section(
            "runtime-relationships",
            "Hosting, ownership and runtime boundaries",
            view.relationships(
                (
                    EdgeKind.HOSTED_BY,
                    EdgeKind.DEPLOYS_TO,
                    EdgeKind.DEPLOYMENT_OWNER,
                    EdgeKind.OWNED_BY,
                    EdgeKind.EXPOSES,
                    EdgeKind.USES_STORAGE,
                    EdgeKind.READS_DATABASE,
                )
            ),
            "No runtime relationship or ownership boundaries are declared.",
        ),
    )
    return view.document(
        DocumentKind.RUNTIME, "Runtime & Infrastructure Architecture", sections, config
    )
