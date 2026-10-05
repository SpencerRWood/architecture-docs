"""Six deterministic architecture documents, independent of publication."""

from architecture_docs.reconciliation import Snapshot
from architecture_docs.renderers import (
    automation,
    catalog,
    data,
    deployment,
    overview,
    runtime,
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
        for renderer in (overview, catalog, deployment, runtime, data, automation)
    )
    return DocumentSet(snapshot.id, documents)
