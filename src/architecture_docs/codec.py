"""Version-checked JSON boundaries for collection and persisted snapshot evidence."""

import json
from dataclasses import asdict
from typing import Any

from architecture_docs.declarations import EdgeKind, NodeKind
from architecture_docs.estate import evaluate
from architecture_docs.graph import (
    Candidate,
    Edge,
    Evidence,
    Gap,
    Graph,
    Node,
    Property,
    node_id,
    resolve,
)
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


def graph_from_data(data: dict[str, Any], evidence: tuple[Evidence, ...]) -> Graph:
    """Read the frozen projection, not today's normalization of historical facts.

    Snapshot IDs bind the complete record in the store. Structural validation
    additionally binds every graph citation and candidate to snapshot evidence.
    """
    if (
        set(data) != {"nodes", "edges", "gaps", "schema_version"}
        or data["schema_version"] != 1
    ):
        raise ValueError("unsupported graph record")
    known = {json.dumps(asdict(item), sort_keys=True): item for item in evidence}

    def citations(items: list[dict[str, Any]]) -> tuple[Evidence, ...]:
        return tuple(known[json.dumps(item, sort_keys=True)] for item in items)

    nodes = []
    for item in data["nodes"]:
        properties = []
        for prop in item["properties"]:
            candidates = tuple(
                Candidate(candidate["value"], citations(candidate["evidence"]))
                for candidate in prop["candidates"]
            )
            result = Property(
                prop["name"], candidates, prop["preferred"], prop["state"]
            )
            if result != resolve(
                result.name,
                {candidate.value: list(candidate.evidence) for candidate in candidates},
            ):
                raise ValueError("invalid property resolution")
            properties.append(result)
        node = Node(
            item["id"],
            NodeKind(item["kind"]),
            item["repository"],
            item["name"],
            item["declared"],
            tuple(properties),
            citations(item["evidence"]),
        )
        if (
            node.id != node_id(node.kind, node.repository, node.name)
            or not node.evidence
            or type(node.declared) is not bool
        ):
            raise ValueError("invalid graph node")
        nodes.append(node)
    identities = {node.id for node in nodes}
    repositories = {
        node.repository for node in nodes if node.kind == NodeKind.REPOSITORY
    }
    if len(identities) != len(nodes) or not {
        item.observation.provenance.repository
        for item in evidence
        if item.observation.key != "estate.contract"
        and not item.observation.key.startswith("infisical.")
    }.issubset(repositories):
        raise ValueError("missing graph identities")
    edges = tuple(
        Edge(
            item["id"],
            EdgeKind(item["kind"]),
            item["source"],
            item["target"],
            citations(item["evidence"]),
        )
        for item in data["edges"]
    )
    if any(
        edge.source not in identities
        or edge.target not in identities
        or not edge.evidence
        for edge in edges
    ):
        raise ValueError("invalid graph endpoints")
    gaps = tuple(Gap(**item) for item in data["gaps"])
    if any(gap.entity not in identities for gap in gaps):
        raise ValueError("invalid graph gap")
    return Graph(tuple(nodes), edges, gaps)


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
            graph_from_data(data["graph"], evidence),
            collection_from_data(data["collection"]),
            policy,
        )
        if snapshot.to_json() != payload:
            raise ValueError("snapshot integrity failure")
        return snapshot
    except ValueError, TypeError, KeyError:
        raise ValueError("invalid snapshot record") from None
