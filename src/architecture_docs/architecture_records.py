"""Typed architecture observation boundary; unknown fields fail without echoing."""

import json
import re

from architecture_docs.classification import CAPABILITIES, DOMAINS, KINDS, description
from architecture_docs.config import Repository
from architecture_docs.declarations import edge_declaration, identifier
from architecture_docs.model import Authority, Observation


def validate(observation: Observation) -> None:  # noqa: PLR0912 - independent typed record variants
    key, value = observation.key, observation.value
    if key.startswith("classification."):
        fields = {
            "classification.domain": DOMAINS,
            "classification.capability": CAPABILITIES.keys(),
            "classification.kind": KINDS,
            "classification.state": {"declared", "inferred"},
        }
        if key == "classification.solution":
            if not re.fullmatch(r"[a-z][a-z0-9-]{0,99}", value):
                raise ValueError("invalid solution identifier")
        elif key not in fields or value not in fields[key]:
            raise ValueError("invalid classification observation")
    elif key == "component.description":
        if description(value) != value:
            raise ValueError("invalid project description")
    elif key == "architecture.relationship":
        data = json.loads(value)
        if (
            not isinstance(data, dict)
            or set(data) != {"kind", "source", "target", "state"}
            or data["state"] not in {"declared", "inferred"}
        ):
            raise ValueError("invalid relationship observation")
        edge_declaration({field: data[field] for field in ("kind", "source", "target")})
    elif key in {
        "deployment.selection",
        "deployment.image",
        "deployment.attested",
        "package.repository",
    }:
        data = json.loads(value)
        fields = {
            "deployment.selection": {"environment", "service"},
            "deployment.image": {"service", "image"},
            "deployment.attested": {"service", "environment", "revision"},
            "package.repository": {"name", "repository"},
        }
        if not isinstance(data, dict) or set(data) != fields[key]:
            raise ValueError("invalid architecture evidence fields")
        for field, item in data.items():
            identifier(item)
            if field == "repository":
                Repository(item, ("*",))
        if key == "deployment.attested" and (
            observation.authority != Authority.GITHUB
            or not re.fullmatch(
                r"github:deployments/[1-9][0-9]*", observation.provenance.source
            )
            or not re.fullmatch(r"[a-f0-9]{40}", data["revision"])
            or data["revision"] != observation.provenance.revision
        ):
            raise ValueError("invalid deployment attestation authority")
    elif key.startswith(("component.", "environment.", "deployment.")) or key in {
        "data.component",
        "orchestration.component",
        "dagster.location",
    }:
        allowed = {
            "component.name",
            "environment.configured",
            "environment.supported",
            "deployment.receipt_selector",
            "data.component",
            "orchestration.component",
            "dagster.location",
        }
        if key not in allowed:
            raise ValueError("unsupported architecture observation")
        identifier(value)
