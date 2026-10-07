"""Version-checked JSON boundaries for collection and persisted snapshot evidence."""

import json
from typing import Any

from architecture_docs.estate import evaluate
from architecture_docs.graph import Evidence, normalize
from architecture_docs.model import (
    Authority,
    CollectionResult,
    Failure,
    Observation,
    Provenance,
    RepositoryInventory,
    SourceCoverage,
)
from architecture_docs.reconciliation import Policy, Snapshot, Tombstone


def observation_from_data(data: dict[str, Any]) -> Observation:
    if (
        not isinstance(data, dict)
        or set(data)
        != {
            "collector",
            "authority",
            "key",
            "value",
            "provenance",
        }
        or not all(
            isinstance(data[field], str) for field in ("collector", "key", "value")
        )
    ):
        raise ValueError("invalid observation shape")
    return Observation(
        data["collector"],
        Authority(data["authority"]),
        data["key"],
        data["value"],
        Provenance(**data["provenance"]),
    )


def collection_from_data(data: dict[str, Any]) -> CollectionResult:
    if (
        not isinstance(data, dict)
        or data.get("schema_version") != 1
        or set(data)
        - {
            "observations",
            "failures",
            "skipped",
            "schema_version",
            "coverage",
            "inventories",
        }
    ):
        raise ValueError("unsupported collection schema")
    if any(
        not isinstance(data.get(field, []), list)
        for field in (
            "observations",
            "failures",
            "skipped",
            "coverage",
            "inventories",
        )
    ):
        raise ValueError("invalid collection shape")
    collection = CollectionResult(
        tuple(observation_from_data(item) for item in data["observations"]),
        tuple(
            Failure(item["collector"], Provenance(**item["provenance"]), item["reason"])
            for item in data["failures"]
        ),
        tuple(data["skipped"]),
        coverage=tuple(SourceCoverage(**item) for item in data.get("coverage", [])),
        inventories=tuple(
            RepositoryInventory(
                item["repository"],
                item["revision"],
                tuple(item["paths"]),
                tuple(item["approvals"]),
            )
            for item in data.get("inventories", [])
        ),
    )
    evaluate(collection)
    return collection


def collection_from_json(payload: str) -> CollectionResult:
    try:
        return collection_from_data(json.loads(payload))
    except ValueError, TypeError, KeyError:
        raise ValueError("invalid collection record") from None


def snapshot_from_json(payload: str) -> Snapshot:
    try:
        data = json.loads(payload)
        if not isinstance(data, dict) or data.get("schema_version") != 1:
            raise ValueError("unsupported snapshot schema")
        evidence = tuple(
            Evidence(observation_from_data(item["observation"]), item["verification"])
            for item in data["evidence"]
        )
        if any(item.verification not in {"verified", "stale"} for item in evidence):
            raise ValueError("invalid verification state")
        policy = Policy(
            tuple(data["policy"]["transient_properties"]),
            tuple(Tombstone(**item) for item in data["policy"]["tombstones"]),
        )
        snapshot = Snapshot(
            evidence,
            normalize(evidence),
            collection_from_data(data["collection"]),
            policy,
        )
        if snapshot.to_json() != payload:
            raise ValueError("snapshot integrity failure")
        return snapshot
    except ValueError, TypeError, KeyError:
        raise ValueError("invalid snapshot record") from None
