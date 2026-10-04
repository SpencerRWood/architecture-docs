"""Common collector interface: inputs contain approved source evidence only."""

from dataclasses import dataclass, field
from typing import Any, Protocol

from architecture_docs.collectors.github import GitHub
from architecture_docs.model import CollectionResult, Provenance


@dataclass(frozen=True)
class SourceFile:
    provenance: Provenance
    content: str = field(repr=False)


@dataclass(frozen=True)
class Context:
    github: GitHub = field(repr=False)
    repository: str
    revision: str
    files: tuple[SourceFile, ...]
    metadata: dict[str, Any] = field(repr=False)
    max_pages: int


class Collector(Protocol):
    name: str

    def collect(self, context: Context) -> CollectionResult: ...
