"""Asset jobs for this code location."""

from dagster import define_asset_job

example_job = define_asset_job("example_job", selection="example_message")
