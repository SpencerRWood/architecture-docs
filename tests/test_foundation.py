"""The renamed foundation packages and runs without external credentials."""

import tomllib
from pathlib import Path

from dagster import Definitions, materialize

from architecture_docs.dagster.assets import repository_observations
from architecture_docs.dagster.definitions import defs


def test_smoke_and_importable_definitions() -> None:
    Definitions.validate_loadable(defs)
    assert defs.resolve_job_def("repository_collection_job")
    result = defs.resolve_job_def("runtime_smoke_job").execute_in_process()
    assert result.success
    assert result.output_for_node("runtime_smoke") == "ok"
    assert not defs.schedules
    assert not defs.sensors


def test_collection_asset_with_empty_offline_registry(tmp_path: Path) -> None:
    path = tmp_path / "registry.toml"
    path.write_text("version=1")
    result = materialize(
        [repository_observations],
        run_config={
            "ops": {
                "repository_observations": {"config": {"registry_path": str(path)}},
            }
        },
    )
    assert result.success
    assert '"observations":[]' in result.output_for_node("repository_observations")


def test_package_release_and_shared_runtime_boundary() -> None:
    root = Path(__file__).parents[1]
    project = tomllib.loads((root / "pyproject.toml").read_text())
    assert project["project"]["name"] == "architecture-docs"
    assert project["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == [
        "src/architecture_docs",
    ]
    assert (root / "src/architecture_docs/py.typed").is_file()
    assert "architecture_docs.dagster.definitions" in (root / "Dockerfile").read_text()
    assert "release.yml@v1" in (root / ".github/workflows/release.yml").read_text()
    assert not any(
        "codex" in dependency for dependency in project["project"]["dependencies"]
    )
    assert project["tool"]["coverage"]["report"]["fail_under"] == 90
