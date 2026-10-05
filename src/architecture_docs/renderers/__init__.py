"""Deterministic architecture, secret metadata and operational documents."""

from architecture_docs.reconciliation import Snapshot
from architecture_docs.renderers import (
    automation,
    catalog,
    data,
    deployment,
    overview,
    runbooks,
    runtime,
    secrets,
)
from architecture_docs.renderers.artifacts import (
    DEFAULT_CONFIG,
    DocumentSet,
    RenderConfig,
)


def render_documents(
    snapshot: Snapshot, config: RenderConfig = DEFAULT_CONFIG
) -> DocumentSet:
    documents = tuple(
        renderer.render(snapshot, config)
        for renderer in (
            overview,
            catalog,
            deployment,
            runtime,
            data,
            automation,
            secrets,
        )
    ) + runbooks.render_all(snapshot, config)
    return DocumentSet(snapshot.id, documents)
