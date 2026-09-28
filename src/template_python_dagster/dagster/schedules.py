"""Application-owned schedules."""

from dagster import ScheduleDefinition

from template_python_dagster.dagster.jobs import example_job

example_schedule = ScheduleDefinition(
    name="example_schedule", job=example_job, cron_schedule="0 9 * * *"
)
