"""Resources and runtime configuration for assets."""

from dagster import ConfigurableResource

from template_python_dagster.config import load_config


class MessageResource(ConfigurableResource["MessageResource"]):
    """Simple typed resource demonstrating configuration injection."""

    message: str


def build_message_resource() -> MessageResource:
    """Resolve configuration when Dagster loads this code location."""
    return MessageResource(message=load_config().message)
