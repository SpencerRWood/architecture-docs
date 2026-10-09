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

    def __post_init__(self) -> None:
        from architecture_docs.architecture_records import validate  # noqa: PLC0415

        validate(self)
        if self.key == "secret.location":
            from architecture_docs.secret_locations import (  # noqa: PLC0415
                SecretLocation,
            )

            SecretLocation(**json.loads(self.value))
        if self.key.startswith("infisical."):
            from architecture_docs.infisical_scope import (  # noqa: PLC0415
                validate_metadata_observation,
            )

            validate_metadata_observation(self.key, self.value)


@dataclass(frozen=True)
class Failure:
    collector: str
    provenance: Provenance
    reason: str


@dataclass(frozen=True, order=True)
class SourceCoverage:
    """Positive evidence that one collector completely read one source."""

    repository: str
    source: str
    collector: str
    revision: str | None


@dataclass(frozen=True, order=True)
class RepositoryInventory:
    """A complete pinned tree within the current approvals, not an account scan."""

    repository: str
    revision: str
    paths: tuple[str, ...]
    approvals: tuple[str, ...]


@dataclass(frozen=True)
class CollectionResult:
    observations: tuple[Observation, ...] = ()
    failures: tuple[Failure, ...] = ()
    skipped: tuple[str, ...] = ()
    schema_version: int = 1
    coverage: tuple[SourceCoverage, ...] = ()
    inventories: tuple[RepositoryInventory, ...] = ()

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
