"""Importable entrypoint for the shared Dagster deployment."""

from dagster import Definitions

from architecture_docs.dagster.assets import repository_observations
from architecture_docs.dagster.jobs import repository_collection_job, runtime_smoke_job

defs = Definitions(
    assets=[repository_observations],
    jobs=[repository_collection_job, runtime_smoke_job],
)
