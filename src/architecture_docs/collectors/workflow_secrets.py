"""Extract caller bindings and usage names; never retain input literals or bodies."""

import json
import re
from typing import Any

from architecture_docs.collectors.github import mapping
from architecture_docs.declarations import canonical

SECRET_EXPRESSION = r"\$\{\{\s*secrets\.([A-Za-z_][A-Za-z0-9_]*)\s*\}\}"  # noqa: S105 - grammar
INPUT_EXPRESSION = r"\$\{\{\s*inputs\.([A-Za-z_][A-Za-z0-9_]*)\s*\}\}"


def forwarded(entries: dict[str, Any]) -> dict[str, str]:
    result = {}
    for alias, value in entries.items():
        if not isinstance(value, str) or not re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_]*", alias
        ):
            continue
        secret = re.fullmatch(SECRET_EXPRESSION, value)
        parameter = re.fullmatch(INPUT_EXPRESSION, value)
        if secret:
            result[alias] = secret[1]
        elif parameter:
            result[alias] = "input:" + parameter[1]
    return result


def secret_facts(data: dict[str, Any]) -> list[tuple[str, str]]:
    facts = []
    triggers = data.get("on", {})
    if (
        isinstance(triggers, (dict, list)) and "workflow_call" in triggers
    ) or triggers == "workflow_call":
        facts.append(("workflow.reusable", "true"))
        call = triggers.get("workflow_call") if isinstance(triggers, dict) else None
        parameters = mapping(call).get("secrets", {}) if isinstance(call, dict) else {}
        for parameter, settings in mapping(parameters).items():
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", parameter):
                continue
            required = (
                settings.get("required", False) if isinstance(settings, dict) else False
            )
            if type(required) is bool:
                facts.append(
                    (
                        "workflow.secret_parameter",
                        canonical({"name": parameter, "required": required}),
                    )
                )
    for name, job in mapping(data.get("jobs", {})).items():
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", name):
            continue
        settings = mapping(job)
        environment = settings.get("environment", "")
        if isinstance(environment, dict):
            environment = environment.get("name", "")
        if not isinstance(environment, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]{1,100}", environment
        ):
            environment = ""
        for secret in sorted(set(re.findall(SECRET_EXPRESSION, json.dumps(settings)))):
            facts.append(
                (
                    "workflow.secret_ref",
                    canonical(
                        {"job": name, "name": secret, "environment": environment}
                    ),
                )
            )
        for parameter in sorted(
            set(re.findall(INPUT_EXPRESSION, json.dumps(settings)))
        ):
            facts.append(
                ("workflow.input_ref", canonical({"job": name, "name": parameter}))
            )
        target = settings.get("uses")
        if (
            not isinstance(target, str)
            or not re.fullmatch(r"[A-Za-z0-9_./@:+*-]{1,200}", target)
            or ".github/workflows/" not in target
        ):
            if settings.get("secrets") == "inherit":
                facts.append(
                    ("workflow.secret_gap", "inherited_secret_caller_unavailable")
                )
            continue
        entries = (
            {}
            if settings.get("secrets") == "inherit"
            else mapping(settings.get("secrets", {}))
        )
        facts.append(
            (
                "workflow.call",
                canonical(
                    {
                        "job": name,
                        "target": target,
                        "inherit": settings.get("secrets") == "inherit",
                        "secrets": forwarded(entries),
                        "inputs": forwarded(mapping(settings.get("with", {}))),
                    }
                ),
            )
        )
    return facts
