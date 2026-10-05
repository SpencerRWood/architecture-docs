"""Repository collection and secret-free infrastructure smoke jobs."""

from dagster import define_asset_job, in_process_executor, job, mem_io_manager, op

from architecture_docs.dagster.assets import (
    architecture_snapshot,
    repository_observations,
)


@op
def runtime_smoke() -> str:
    return "ok"


@job(executor_def=in_process_executor, resource_defs={"io_manager": mem_io_manager})
def runtime_smoke_job() -> None:
    """No external access, tokens, collection, or application side effects."""
    runtime_smoke()


repository_collection_job = define_asset_job(
    "repository_collection_job",
    selection=[repository_observations.key],
)

architecture_reconciliation_job = define_asset_job(
    "architecture_reconciliation_job",
    selection=[repository_observations.key, architecture_snapshot.key],
)
