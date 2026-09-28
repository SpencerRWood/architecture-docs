"""Exercise the template asset through its Dagster definitions."""

from dagster import materialize

from template_python_dagster.dagster.assets import example_message
from template_python_dagster.dagster.resources import MessageResource


def test_example_asset_materializes_without_services() -> None:
    result = materialize(
        [example_message],
        resources={"message_resource": MessageResource(message="test")},
    )

    assert result.success
    assert result.output_for_node("example_message") == "test"
