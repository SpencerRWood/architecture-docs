"""Reviewed architecture declaration vocabulary: identifiers, never source bodies."""

import json
import re
from enum import StrEnum
from typing import Any

from architecture_docs.config import Repository


class NodeKind(StrEnum):
    REPOSITORY = "repository"
    SYSTEM = "system"
    SERVICE = "service"
    ENVIRONMENT = "environment"
    HOST = "host"
    DATABASE = "database"
    STORAGE = "storage"
    RELEASE_CONTRACT = "release_contract"
    ORCHESTRATION = "orchestration"
    INTERFACE = "interface"
    SECRET_REFERENCE = "secret_reference"  # noqa: S105 - schema type, not a value
    OWNER = "owner"
    PROCEDURE = "procedure"
    PACKAGE = "package"


class EdgeKind(StrEnum):
    CONTAINS = "contains"
    DEPENDS_ON = "depends_on"
    DEPLOYMENT_OWNER = "deployment_owner"
    DEPLOYS_TO = "deploys_to"
    CONSUMES_WORKFLOW = "consumes_workflow"
    HOSTED_BY = "hosted_by"
    READS_DATABASE = "reads_database"
    USES_STORAGE = "uses_storage"
    NOTIFIES = "notifies"
    CONSUMES_SECRET = "consumes_secret"  # noqa: S105 - relationship type
    ORCHESTRATES = "orchestrates"
    EXPOSES = "exposes"
    OWNED_BY = "owned_by"
    OPERATED_BY = "operated_by"


# No free-form prose, arbitrary commands, credentials, or secret-value field.
FIELDS = frozenset(
    {
        "purpose",
        "technology",
        "lifecycle",
        "environment",
        "path",
        "project",
        "scope",
        "injection",
        "required",
        "script",
        "workflow",
        "prerequisite",
        "verification",
        "rollback",
        "recovery",
        "role",
        "schema",
        "backup",
        "endpoint",
        "protocol",
        "port",
        "deployment_path",
        "owner",
        "host",
        "collected_at",
        "last_seen",
        "tag",
        "archived",
        "reference",
        "kind",
        "checks",
        "python_package",
        "semantic_release",
        "runtime_validation",
        "publish",
        "base_image",
        "executable",
        "declaration",
        "source_digest",
    }
)


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def identifier(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(
        r"[A-Za-z0-9_./@+*-][A-Za-z0-9_./@:+*-]{0,199}", value
    ):
        raise ValueError("invalid architecture identifier")
    if "://" in value or ".." in value.split("/"):
        raise ValueError("invalid architecture path")
    return value


def node_declaration(data: dict[str, Any]) -> dict[str, Any]:
    if set(data) - {"kind", "name", "attributes"}:
        raise ValueError("unknown node declaration field")
    kind = NodeKind(data["kind"])
    attributes = data.get("attributes", {})
    if not isinstance(attributes, dict) or set(attributes) - FIELDS:
        raise ValueError("unknown architecture attribute")
    return {
        "kind": kind.value,
        "name": identifier(data["name"]),
        "attributes": {key: identifier(value) for key, value in attributes.items()},
    }


def reference(data: dict[str, Any]) -> dict[str, str]:
    if set(data) - {"kind", "name", "repository"}:
        raise ValueError("unknown architecture reference field")
    result = {"kind": NodeKind(data["kind"]).value, "name": identifier(data["name"])}
    if "repository" in data:
        result["repository"] = identifier(data["repository"])
    if result["kind"] == NodeKind.REPOSITORY:
        result["repository"] = result.get("repository", result["name"])
        if result["name"] != result["repository"]:
            raise ValueError("repository reference identity mismatch")
    if "repository" in result:
        Repository(result["repository"], ("*",))
    return result


def edge_declaration(data: dict[str, Any]) -> dict[str, Any]:
    if set(data) != {"kind", "source", "target"}:
        raise ValueError("invalid edge declaration")
    return {
        "kind": EdgeKind(data["kind"]).value,
        "source": reference(data["source"]),
        "target": reference(data["target"]),
    }


def architecture_declarations(data: dict[str, Any]) -> list[tuple[str, str]]:
    """Optional architecture.toml closes explicit gaps without guessing topology."""
    if set(data) - {"version", "nodes", "edges"} or data.get("version") != 1:
        raise ValueError("unsupported architecture declaration")
    return [
        *(
            ("architecture.node", canonical(node_declaration(node)))
            for node in data.get("nodes", [])
        ),
        *(
            ("architecture.edge", canonical(edge_declaration(edge)))
            for edge in data.get("edges", [])
        ),
    ]
