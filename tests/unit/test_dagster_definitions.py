"""Check the standalone Dagster code location contract."""

from dagster import Definitions

from template_python_dagster.config import AppConfig, load_config
from template_python_dagster.dagster.definitions import defs


def test_config_uses_injected_environment() -> None:
    assert load_config({"APP_MESSAGE": "configured"}) == AppConfig("configured")
    assert load_config({}) == AppConfig("Hello from Dagster")


def test_definitions_load_and_register_examples() -> None:
    assert isinstance(defs, Definitions)
    Definitions.validate_loadable(defs)
    assert defs.resolve_job_def("example_job").name == "example_job"
    assert defs.resolve_schedule_def("example_schedule").name == "example_schedule"
    keys = defs.resolve_asset_graph().get_all_asset_keys()
    assert {key.to_user_string() for key in keys} == {"example_message"}
