# template-python-dagster

A typed Python starting point for Dagster code locations that run scheduled, asset-oriented, sensor-driven, or otherwise orchestrated workloads. It is suitable for reporting, ingestion, sync, and similar workflows. The examples run without external services.

## Choose a template

| Template | Use it for |
| --- | --- |
| `template-python-analytics` | Reusable analysis and transformation workflows without orchestration. |
| `template-python-dagster` | Scheduled, asset-oriented, sensor-driven, or orchestrated workloads. |
| `template-fastapi-service` | Long-running HTTP/API applications. |

## Structure

```text
src/template_python_dagster/
  __init__.py
  py.typed
  config.py
  models.py
  transforms.py
  validation.py
  io/
    __init__.py
    readers.py
    writers.py
  dagster/
    __init__.py
    assets.py
    jobs.py
    resources.py
    schedules.py
    sensors.py
    definitions.py
tests/
Dockerfile
.github/release.toml
```

`dagster/definitions.py` exports `defs`, a `dagster.Definitions` object. The one example asset, job, resource, and schedule show registration and execution. Delete any examples you do not need and remove their imports and entries from `defs`; no other architecture needs to change. `sensors.py` is empty until a consuming project needs a sensor. The other package modules remain small placeholders for application logic.

## Local development

Python 3.14, `uv`, Hatchling, Ruff, strict mypy, pytest, pre-commit, and conventional commits follow the analytics template.

```sh
uv lock
uv sync --frozen --group dev
uv run pre-commit install
uv run dagster dev -m template_python_dagster.dagster.definitions
```

The local Dagster command starts the UI and daemon. The example schedule is defined in code; enable it in the UI if you want it to run. It does not need an external service. Set `APP_MESSAGE` to change the example resource value. Add real runtime settings as typed fields in `config.py`; inject secrets through environment variables at deployment time (for example, with Infisical). Do not put credentials in source control.

Run the quality gates:

```sh
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
uv build
uv run pre-commit run --all-files
```

## Container and shared deployment

Build and run the code-location gRPC server:

```sh
docker build -t template-python-dagster .
docker run --rm -e DAGSTER_GRPC_PORT=4000 -p 4000:4000 template-python-dagster
```

The container loads `template_python_dagster.dagster.definitions` with `dagster api grpc`. It takes the listener port from `DAGSTER_GRPC_PORT`; choose that port in the consuming deployment. The image has no deployment hostname, secrets, or service address. Keep the Dagster version compatible with the shared deployment and provide any application configuration at runtime.

The consuming application owns its assets, jobs, schedules, and sensors. The shared infrastructure repository only builds/deploys the image, injects runtime environment and secrets, and registers its gRPC endpoint as a code location in the Dagster workspace. For example, infrastructure can configure a `grpc_server` entry with `host`, `port`, and `location_name` matching its deployed container; those values belong in infrastructure configuration, not this template. The shared Dagster daemon evaluates schedules and sensors registered from the application code location. Infrastructure should not duplicate their definitions.

The release caller uses the centralized `SpencerRWood/workflows` release and validation contracts at `@v1`. A copied project can use the shared container release workflow to publish its own image after a release.

## Copy and rename

1. Create a new repository and copy this template's tracked files.
2. Replace `template-python-dagster` with the new distribution/repository name and `template_python_dagster` with the new import package name in `pyproject.toml`, `src/`, `tests/`, `Dockerfile`, `.github/`, and this README. Rename the package directory. Keep the module path in the Docker command and local Dagster command aligned.
3. Update the package description, runtime settings, assets, resources, jobs, schedules, and sensors for the application. Remove unused examples and their `Definitions` entries.
4. Run `uv lock`, `uv sync --frozen --group dev`, and the quality gates above. Build and test the container before registering its code location in shared infrastructure.

The semantic-release setup uses conventional commits and `v`-prefixed tags. `.github/release.toml` declares Python package validation and build capabilities for the centralized workflow.
