"""The renamed foundation packages and runs without external credentials."""

import json
import tomllib
from pathlib import Path

import pytest
from dagster import Definitions, materialize

from architecture_docs.codec import snapshot_from_json
from architecture_docs.dagster.assets import repository_observations
from architecture_docs.dagster.definitions import defs
from architecture_docs.store import SnapshotStore


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


def test_manual_reconciliation_job_is_restart_safe_and_has_no_schedule(
    tmp_path: Path,
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARCHITECTURE_DOCS_DATABASE_URL", database_url)
    path = tmp_path / "registry.toml"
    path.write_text("version=1")
    policy = tmp_path / "policy.toml"
    policy.write_text("version=1")
    config = {
        "ops": {
            "repository_observations": {"config": {"registry_path": str(path)}},
            "architecture_snapshot": {"config": {"policy_path": str(policy)}},
        }
    }
    job = defs.resolve_job_def("architecture_reconciliation_job")
    result = job.execute_in_process(run_config=config)
    assert result.success
    snapshot = snapshot_from_json(result.output_for_node("architecture_snapshot"))
    assert SnapshotStore().latest() == snapshot
    config["ops"]["architecture_snapshot"]["config"].pop("policy_path")
    repeated = job.execute_in_process(run_config=config)
    assert repeated.success
    assert repeated.output_for_node("architecture_snapshot") == snapshot.to_json()
    assert not defs.schedules


def test_manual_rendering_job_emits_documents_without_external_publication(
    tmp_path: Path,
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARCHITECTURE_DOCS_DATABASE_URL", database_url)
    path = tmp_path / "registry.toml"
    path.write_text("version=1")
    result = defs.resolve_job_def("architecture_rendering_job").execute_in_process(
        run_config={
            "ops": {
                "repository_observations": {"config": {"registry_path": str(path)}},
                "architecture_snapshot": {"config": {}},
            }
        },
    )
    assert result.success
    artifacts = json.loads(result.output_for_node("architecture_documents"))
    assert len(artifacts["documents"]) == 15
    snapshot = SnapshotStore().latest()
    assert snapshot is not None
    assert artifacts["snapshot_id"] == snapshot.id
    assert all(
        doc["generation_state"] == "incomplete_estate" for doc in artifacts["documents"]
    )
    assert not defs.schedules
    assert not defs.sensors
