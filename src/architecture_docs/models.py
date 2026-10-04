"""Shared application model placeholders."""

from dataclasses import dataclass


@dataclass(frozen=True)
class AnalysisRecord:
    """Placeholder record shape for workflow inputs."""

    raw: dict[str, object]
