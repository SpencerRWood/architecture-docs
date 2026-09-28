"""Small asset example; replace with application assets."""

from dagster import AssetExecutionContext, asset

from template_python_dagster.dagster.resources import MessageResource


@asset
def example_message(
    context: AssetExecutionContext, message_resource: MessageResource
) -> str:
    """Materialize one configured message without an external service."""
    message = message_resource.message
    context.log.info(message)
    return message
