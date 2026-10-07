"""Configured estate expectations, independent of successful source collection."""

import json
import re
from dataclasses import asdict, dataclass
from hashlib import sha256
from typing import Any

from architecture_docs.declarations import NodeKind, canonical
from architecture_docs.model import Authority, CollectionResult, Observation, Provenance


@dataclass(frozen=True, order=True)
class ExpectedRepository:
    name: str
    classification: str
    required: bool

    def __post_init__(self) -> None:
        if (
            not isinstance(self.name, str)
            or not re.fullmatch(
                r"[A-Za-z0-9_-][A-Za-z0-9_.-]*/[A-Za-z0-9_-][A-Za-z0-9_.-]*",
                self.name,
            )
            or self.classification not in {"core", "service", "supporting"}
            or type(self.required) is not bool
            or (self.classification == "core" and not self.required)
        ):
            raise ValueError("invalid estate repository")


@dataclass(frozen=True, order=True)
class RequiredDomain:
    name: str
    node_kinds: tuple[str, ...] = ()
    fact_prefixes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (
            not isinstance(self.name, str)
            or not re.fullmatch(r"[a-z][a-z_]{0,63}", self.name)
            or not (self.node_kinds or self.fact_prefixes)
            or not isinstance(self.node_kinds, tuple)
            or not isinstance(self.fact_prefixes, tuple)
            or not all(isinstance(value, str) for value in self.node_kinds)
            or set(self.node_kinds) - set(NodeKind)
            or len(set(self.node_kinds)) != len(self.node_kinds)
            or any(
                not isinstance(value, str)
                or not re.fullmatch(r"[a-z][a-z_.]{0,63}", value)
                for value in self.fact_prefixes
            )
            or len(set(self.fact_prefixes)) != len(self.fact_prefixes)
        ):
            raise ValueError("invalid estate domain")
        object.__setattr__(self, "node_kinds", tuple(sorted(self.node_kinds)))
        object.__setattr__(self, "fact_prefixes", tuple(sorted(self.fact_prefixes)))


@dataclass(frozen=True)
class EstateContract:
    repositories: tuple[ExpectedRepository, ...]
    domains: tuple[RequiredDomain, ...]

    def __post_init__(self) -> None:
        if (
            not self.repositories
            or not self.domains
            or len({item.name.casefold() for item in self.repositories})
            != len(self.repositories)
            or len({item.name for item in self.domains}) != len(self.domains)
            or len(self.repositories) > 100
            or len(self.domains) > 20
        ):
            raise ValueError("duplicate or unbounded estate expectations")
        object.__setattr__(self, "repositories", tuple(sorted(self.repositories)))
        object.__setattr__(self, "domains", tuple(sorted(self.domains)))

    def observation(self) -> Observation:
        return Observation(
            "estate",
            Authority.CONFIGURATION,
            "estate.contract",
            canonical(asdict(self)),
            contract_provenance(),
        )


def contract_from_data(data: dict[str, Any]) -> EstateContract:
    if not isinstance(data, dict) or set(data) != {"repositories", "domains"}:
        raise ValueError("invalid estate contract")
    if any(not isinstance(data[field], list) for field in ("repositories", "domains")):
        raise ValueError("invalid estate lists")
    repositories = []
    for item in data["repositories"]:
        if not isinstance(item, dict) or set(item) != {
            "name",
            "classification",
            "required",
        }:
            raise ValueError("invalid estate repository shape")
        repositories.append(ExpectedRepository(**item))
    domains = []
    for item in data["domains"]:
        if (
            not isinstance(item, dict)
            or "name" not in item
            or set(item) - {"name", "node_kinds", "fact_prefixes"}
            or any(
                not isinstance(item.get(field, []), list)
                or not all(isinstance(value, str) for value in item.get(field, []))
                for field in ("node_kinds", "fact_prefixes")
            )
        ):
            raise ValueError("invalid estate domain shape")
        domain = RequiredDomain(
            item["name"],
            tuple(item.get("node_kinds", [])),
            tuple(item.get("fact_prefixes", [])),
        )
        domains.append(
            RequiredDomain(
                domain.name,
                tuple(sorted(domain.node_kinds)),
                tuple(sorted(domain.fact_prefixes)),
            )
        )
    return EstateContract(
        tuple(sorted(repositories)),
        tuple(sorted(domains)),
    )


@dataclass(frozen=True)
class RepositoryCoverage:
    name: str
    classification: str
    required: bool
    represented: bool
    reason: str


@dataclass(frozen=True)
class DomainCoverage:
    name: str
    repositories: tuple[str, ...]


@dataclass(frozen=True)
class EstateCoverage:
    configured: bool
    repositories: tuple[RepositoryCoverage, ...] = ()
    domains: tuple[DomainCoverage, ...] = ()
    contract_id: str | None = None

    @property
    def complete(self) -> bool:
        return (
            self.configured
            and all(not item.required or item.represented for item in self.repositories)
            and all(item.repositories for item in self.domains)
        )


def evaluate(collection: CollectionResult) -> EstateCoverage:
    contracts = [
        item for item in collection.observations if item.key == "estate.contract"
    ]
    if not contracts:
        return EstateCoverage(False)
    if (
        len(contracts) != 1
        or contracts[0].collector != "estate"
        or contracts[0].authority != Authority.CONFIGURATION
        or contracts[0].provenance != contract_provenance()
    ):
        raise ValueError("ambiguous estate contract")
    contract = contract_from_data(json.loads(contracts[0].value))
    covered = {
        (item.repository, item.source, item.collector, item.revision)
        for item in collection.coverage
    }
    if len({scope[:3] for scope in covered}) != len(covered):
        raise ValueError("conflicting source coverage")
    inventory_records = set(collection.inventories)
    if len({item.repository for item in inventory_records}) != len(inventory_records):
        raise ValueError("conflicting repository inventories")
    present = (
        {
            item.provenance.repository
            for item in collection.observations
            if item.collector != "estate"
        }
        | {item.repository for item in collection.coverage}
        | {item.repository for item in collection.inventories}
    )
    if present.intersection(collection.skipped):
        raise ValueError("skipped repository has current evidence")
    inventories = {item.repository: item for item in collection.inventories}
    facts = tuple(
        item
        for item in collection.observations
        if item.collector != "estate"
        and not item.provenance.source.startswith("github:")
        and item.provenance.revision is not None
        and (
            item.provenance.repository not in inventories
            or (
                inventories[item.provenance.repository].revision
                == item.provenance.revision
                and item.provenance.source
                in inventories[item.provenance.repository].paths
            )
        )
        and (
            item.provenance.repository,
            item.provenance.source,
            item.collector,
            item.provenance.revision,
        )
        in covered
        and not any(
            failure.provenance.repository == item.provenance.repository
            for failure in collection.failures
        )
    )
    represented = {
        expected.name
        for expected in contract.repositories
        if any(
            item.provenance.repository == expected.name
            and (
                expected.classification == "supporting"
                or (
                    item.authority <= Authority.EXECUTABLE and item.key != "source.kind"
                )
            )
            for item in facts
        )
    }
    failed = {item.provenance.repository for item in collection.failures}
    repositories = tuple(
        RepositoryCoverage(
            item.name,
            item.classification,
            item.required,
            item.name in represented,
            "represented"
            if item.name in represented
            else "source_failed"
            if item.name in failed
            else "skipped"
            if item.name in collection.skipped
            else "no_current_architecture_evidence",
        )
        for item in contract.repositories
    )
    expected = {item.name for item in contract.repositories}
    domains = tuple(
        DomainCoverage(
            domain.name,
            tuple(
                sorted(
                    {
                        item.provenance.repository
                        for item in facts
                        if item.provenance.repository in expected
                        and (
                            any(
                                item.key.startswith(prefix)
                                for prefix in domain.fact_prefixes
                            )
                            or (
                                item.key == "architecture.node"
                                and json.loads(item.value)["kind"] in domain.node_kinds
                            )
                        )
                    }
                )
            ),
        )
        for domain in contract.domains
    )
    return EstateCoverage(
        True,
        repositories,
        domains,
        sha256(canonical(asdict(contract)).encode()).hexdigest(),
    )


def contract_provenance() -> Provenance:
    """The registry is local configuration, never a collected repository fact."""
    return Provenance(
        "SpencerRWood/architecture-docs", "config/repositories.toml", None
    )
