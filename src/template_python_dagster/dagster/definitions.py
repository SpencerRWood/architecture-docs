"""Importable entrypoint for the shared Dagster deployment."""

from dagster import Definitions

from template_python_dagster.dagster.assets import example_message
from template_python_dagster.dagster.jobs import example_job
from template_python_dagster.dagster.resources import build_message_resource
from template_python_dagster.dagster.schedules import example_schedule

defs = Definitions(
    assets=[example_message],
    jobs=[example_job],
    schedules=[example_schedule],
    resources={"message_resource": build_message_resource()},
)
