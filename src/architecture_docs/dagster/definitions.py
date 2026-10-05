"""Importable entrypoint for the shared Dagster deployment."""

from dagster import Definitions

from architecture_docs.dagster.assets import (
    architecture_documents,
    architecture_snapshot,
    repository_observations,
)
from architecture_docs.dagster.jobs import (
    architecture_reconciliation_job,
    architecture_rendering_job,
    repository_collection_job,
    runtime_smoke_job,
)

defs = Definitions(
    assets=[repository_observations, architecture_snapshot, architecture_documents],
    jobs=[
        repository_collection_job,
        architecture_reconciliation_job,
        architecture_rendering_job,
        runtime_smoke_job,
    ],
)
