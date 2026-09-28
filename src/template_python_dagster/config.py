"""Typed runtime configuration loaded from environment variables."""

import os
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class AppConfig:
    """Example non-secret setting; extend in the consuming repository."""

    message: str


def load_config(environ: Mapping[str, str] | None = None) -> AppConfig:
    """Read runtime configuration; an explicit mapping keeps this testable."""
    values = os.environ if environ is None else environ
    return AppConfig(message=values.get("APP_MESSAGE", "Hello from Dagster"))
