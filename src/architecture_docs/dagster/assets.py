"""Manual collection boundary; scheduling and durable reconciliation come later."""

import os
from pathlib import Path

from dagster import AssetExecutionContext, Config, asset

from architecture_docs.collection import collect
from architecture_docs.collectors.github import GitHub
from architecture_docs.config import load_registry


class CollectionConfig(Config):
    registry_path: str


@asset
def repository_observations(
    context: AssetExecutionContext, config: CollectionConfig
) -> str:
    github = GitHub(os.environ.get("ARCHITECTURE_DOCS_GITHUB_TOKEN"))
    try:
        result = collect(load_registry(Path(config.registry_path)), github)
    finally:
        github.close()
    context.add_output_metadata(
        {
            "complete": result.complete,
            "observation_count": len(result.observations),
            "failure_count": len(result.failures),
            "schema_version": result.schema_version,
        }
    )
    return result.to_json()
