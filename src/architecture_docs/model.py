"""Versioned collector contracts, independent of Dagster or graph semantics."""

import json
from dataclasses import asdict, dataclass
from enum import IntEnum


class Authority(IntEnum):
    """Lower numbers have higher authority; retain all conflicting evidence."""

    CONFIGURATION = 0
    EXECUTABLE = 1
    DOCUMENTATION = 2
    GITHUB = 3


@dataclass(frozen=True, order=True)
class Provenance:
    repository: str
    source: str
    revision: str | None
    blob: str | None = None


@dataclass(frozen=True)
class Observation:
    collector: str
    authority: Authority
    key: str
    value: str
    provenance: Provenance


@dataclass(frozen=True)
class Failure:
    collector: str
    provenance: Provenance
    reason: str


@dataclass(frozen=True)
class CollectionResult:
    observations: tuple[Observation, ...] = ()
    failures: tuple[Failure, ...] = ()
    skipped: tuple[str, ...] = ()
    schema_version: int = 1

    @property
    def complete(self) -> bool:
        return not self.failures

    def to_json(self) -> str:
        """Stable serialization; no clock, source bodies, or exception messages."""
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))


def precedence(observations: tuple[Observation, ...]) -> tuple[Observation, ...]:
    """Order evidence by authority, never discard or infer conflict resolution."""
    return tuple(
        sorted(
            observations,
            key=lambda item: (
                item.authority,
                item.provenance.repository,
                item.provenance.source,
                item.key,
                item.value,
                item.collector,
            ),
        )
    )
