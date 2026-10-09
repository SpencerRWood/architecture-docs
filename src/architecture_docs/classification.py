"""Minimal metadata vocabulary and deterministic, evidence-labelled groups."""

import re
from typing import Any

from architecture_docs.declarations import identifier

GROUPS = (
    "Infrastructure & Hosting",
    "Shared Platform Services",
    "Developer Tooling",
    "Agent Configuration & Runtime",
    "Release & Promotion Control",
    "Reporting & Documentation",
    "Applications & Reference Solutions",
    "Engineering Templates",
)
DOMAINS = frozenset(
    {
        "engineering",
        "platform",
        "operations",
        "analytics",
        "applications",
        "infrastructure",
        "platform-services",
        "reference-solutions",
        "reporting",
        "templates",
    }
)
CAPABILITIES = {
    "infrastructure": GROUPS[0],
    "hosting": GROUPS[0],
    "shared-services": GROUPS[1],
    "events": GROUPS[1],
    "retrieval": GROUPS[1],
    "developer-tooling": GROUPS[2],
    "agent-configuration": GROUPS[3],
    "agent-runtime": GROUPS[3],
    "release-control": GROUPS[4],
    "promotion-control": GROUPS[4],
    "reporting": GROUPS[5],
    "documentation": GROUPS[5],
    "application": GROUPS[6],
    "reference-solution": GROUPS[6],
    "data-generation": GROUPS[6],
    "data-transformation": GROUPS[6],
    "analytics": GROUPS[6],
    "engineering-template": GROUPS[7],
    "hosting-deployment": GROUPS[0],
    "event-messaging": GROUPS[1],
    "knowledge-retrieval": GROUPS[1],
    "release-promotion": GROUPS[4],
    "reliability-recovery": GROUPS[4],
    "architecture-documentation": GROUPS[5],
    "document-rendering": GROUPS[5],
    "operational-reporting": GROUPS[5],
    "visualization": GROUPS[5],
    "web-application": GROUPS[6],
    "analytics-lifecycle": GROUPS[6],
    "project-scaffolding": GROUPS[7],
}
KINDS = frozenset(
    {
        "cli",
        "library",
        "service",
        "web-app",
        "pipeline",
        "orchestrator",
        "infrastructure",
        "configuration",
        "workflow",
        "report",
        "template",
        "data-model",
        "agent-runtime",
        "data-generator",
        "repository",
        "web-application",
        "orchestration-service",
        "workflow-library",
        "infrastructure-configuration",
        "api-service",
        "analytics-workspace",
        "dbt-project",
        "solution-overview",
        "dbt-template",
        "fastapi-react-template",
        "fastapi-service-template",
        "python-analytics-template",
        "python-api-client-template",
        "python-cli-template",
        "python-dagster-template",
        "python-library-template",
        "synthetic-data-template",
        "web-automation-template",
    }
)
KNOWN_ROLES = {
    "infrastructure": "infrastructure",
    "homelab": "hosting",
    "wood-tools": "developer-tooling",
    "sql-control-cli": "developer-tooling",
    "wood-agents": "agent-configuration",
    "codex-config": "agent-configuration",
    "pi-config": "agent-configuration",
    "codex-runtime": "agent-runtime",
    "workflows": "release-control",
    "wood-reports": "reporting",
    "wood-charts": "reporting",
    "openproject-reports": "reporting",
    "architecture-docs": "documentation",
    "events-service": "events",
    "rag-service": "retrieval",
}
REFERENCE_COMPONENTS = frozenset(
    {
        "synthetic-website-data",
        "synthetic-website-dbt",
        "synthetic-website-analytics",
        "synthetic-website-analytics-platform",
    }
)


def metadata(data: Any) -> dict[str, str]:
    if not isinstance(data, dict) or set(data) - {
        "domain",
        "capability",
        "kind",
        "solution",
    }:
        raise ValueError("invalid architecture metadata fields")
    if not all(
        isinstance(data.get(field), str) for field in ("domain", "capability", "kind")
    ):
        raise ValueError("invalid architecture vocabulary")
    if (
        data.get("domain") not in DOMAINS
        or data.get("capability") not in CAPABILITIES
        or data.get("kind") not in KINDS
    ):
        raise ValueError("invalid architecture vocabulary")
    if "solution" in data and (
        not isinstance(data["solution"], str)
        or not re.fullmatch(r"[a-z][a-z0-9-]{0,99}", data["solution"])
    ):
        raise ValueError("invalid solution identifier")
    return {key: identifier(value) for key, value in data.items()}


def role(repository: str) -> str | None:
    name = repository.rsplit("/", 1)[-1]
    if name.startswith("template-"):
        return "engineering-template"
    if name.startswith("synthetic-website-"):
        return "reference-solution"
    return KNOWN_ROLES.get(name)


def description(value: object) -> str | None:
    """Keep a bounded project description, never URLs, assignments or tokens."""
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    if (
        not text
        or len(text) > 300
        or not re.fullmatch(r"[\w ,.;()&'/-]+", text)
        or re.search(
            r"\w{40,}|(?:token|password|credential|secret)\s+[A-Za-z0-9_-]{16,}",
            text,
            re.I,
        )
    ):
        return None
    return text
