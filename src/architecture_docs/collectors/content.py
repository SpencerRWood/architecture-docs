"""Extract approved structure, not raw documents, commands, or secret literals."""

import ast
import json
import re
import shlex
import tomllib
from collections.abc import Callable
from pathlib import PurePosixPath
from typing import Any

import yaml

from architecture_docs.collectors.contracts import Context, SourceFile
from architecture_docs.collectors.github import mapping
from architecture_docs.model import Authority, CollectionResult, Failure, Observation

Facts = list[tuple[str, str]]


def identifiers(values: object) -> list[str]:
    """Only declared identifiers; no descriptions or arbitrary literal values."""
    candidates = values if isinstance(values, (list, dict)) else []
    return sorted(
        {
            item
            for item in candidates
            if isinstance(item, str)
            and re.fullmatch(r"[A-Za-z0-9_./@:+*-]{1,200}", item)
        }
    )


def parsed(source: SourceFile) -> dict[str, Any]:
    if source.provenance.source.endswith(".toml"):
        return tomllib.loads(source.content)
    if source.provenance.source.endswith(".json"):
        return mapping(json.loads(source.content))
    return mapping(yaml.safe_load(source.content))


def configuration(source: SourceFile) -> Facts:
    path = source.provenance.source
    if PurePosixPath(path).name == "Dockerfile":
        return [
            ("container.base_image", value)
            for value in identifiers(
                re.findall(
                    r"(?im)^FROM\s+([^\s]+)",
                    source.content,
                )
            )
        ]
    if path.endswith(".tf"):
        return [
            ("terraform.declaration", f"{kind}:{type_name}:{name}")
            for kind, type_name, name in re.findall(
                r'^\s*(resource|data)\s+"([\w-]+)"\s+"([\w-]+)"\s*\{',
                source.content,
                re.MULTILINE,
            )
        ]
    if path.endswith((".yml", ".yaml")):
        decoded = yaml.safe_load(source.content)
        if isinstance(decoded, list):
            return ansible(decoded)
    return structured_configuration(parsed(source), path)


def structured_configuration(data: dict[str, Any], path: str) -> Facts:
    facts: Facts = []
    project = mapping(data.get("project", {}))
    if package_names := identifiers([project.get("name")]):
        facts.append(("package.name", package_names[0]))
    for dependency in project.get("dependencies", []):
        if isinstance(dependency, str) and (match := re.match(r"[\w.-]+", dependency)):
            facts.append(("package.dependency", match[0].lower()))
    if path.endswith("package.json"):
        facts.extend(
            ("package.name", value) for value in identifiers([data.get("name")])
        )
        for group in ("dependencies", "devDependencies", "peerDependencies"):
            facts.extend(
                (f"package.{group}", value) for value in identifiers(data.get(group))
            )
    for group in ("roles", "collections"):
        facts.extend(
            (f"ansible.{group}", value) for value in identifiers(data.get(group))
        )
    for section in ("services", "volumes", "networks", "jobs"):
        facts.extend(
            (f"compose.{section}", value)
            for value in identifiers(
                data.get(section, {}),
            )
        )
    for name, service in mapping(data.get("services", {})).items():
        if name not in identifiers([name]):
            continue
        settings = mapping(service)
        environment = settings.get("environment", {})
        names = (
            environment
            if isinstance(environment, dict)
            else [
                item.split("=", 1)[0] for item in environment if isinstance(item, str)
            ]
        )
        facts.extend(
            (f"service.{name}.environment_name", value) for value in identifiers(names)
        )
        facts.extend(
            (f"service.{name}.depends_on", value)
            for value in identifiers(settings.get("depends_on", {}))
        )
    # Release contracts expose bounded booleans/check identifiers, never tokens.
    for section in ("validation", "build", "release", "dagster", "container"):
        settings = mapping(data.get(section, {}))
        facts.extend(
            (f"{section}.checks", value)
            for value in identifiers(settings.get("checks", []))
        )
        for key in (
            "python_package",
            "semantic_release",
            "runtime_validation",
            "publish",
        ):
            if isinstance(settings.get(key), bool):
                facts.append((f"{section}.{key}", str(settings[key]).lower()))
    return facts


def ansible(plays: list[Any]) -> Facts:
    """Collect declared roles and module identifiers, never task arguments."""
    facts: Facts = []
    for play in plays:
        settings = mapping(play)
        for role in settings.get("roles", []):
            names = [role.get("role")] if isinstance(role, dict) else [role]
            facts.extend(("ansible.role", value) for value in identifiers(names))
        facts.extend(
            ("ansible.variable_name", value)
            for value in identifiers(settings.get("vars", {}))
        )
        tasks = settings.get("tasks", [])
        if tasks:
            facts.extend(ansible(tasks))
        facts.extend(
            ("ansible.module", key)
            for key in identifiers(settings)
            if key.startswith("ansible.builtin.")
        )
    return facts


def executable(source: SourceFile) -> Facts:
    if not source.provenance.source.startswith(".github/workflows/"):
        # A script is evidence of an executable contract; arbitrary arguments and
        # source strings are intentionally not copied to the observation model.
        suffix = PurePosixPath(source.provenance.source).suffix
        facts: Facts = [("contract.kind", suffix)]
        if suffix == ".py":
            tree = ast.parse(source.content)
            facts.extend(
                ("python.declaration", node.name)
                for node in ast.walk(tree)
                if isinstance(
                    node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
                )
            )
        elif suffix == ".sh":
            for line in source.content.splitlines():
                tokens = shlex.split(line, comments=True)
                if tokens and tokens[0] not in {"echo", "printf", "export", "set"}:
                    facts.extend(
                        ("shell.executable", value)
                        for value in identifiers([tokens[0]])
                        if re.fullmatch(r"[\w./-]+", value)
                    )
        return facts
    data = parsed(source)
    facts = []
    for name, job in mapping(data.get("jobs", {})).items():
        settings = mapping(job)
        facts.extend(("workflow.job", value) for value in identifiers([name]))
        actions = [settings.get("uses")]
        actions.extend(mapping(step).get("uses") for step in settings.get("steps", []))
        for action in identifiers(actions):
            # Only literal action/reusable-workflow references, no expressions.
            if "@" in action or action.startswith("./"):
                facts.append(("workflow.uses", action))
        facts.extend(
            ("workflow.secret_name", value)
            for value in identifiers(settings.get("secrets", {}))
        )
    return facts


def documentation(source: SourceFile) -> Facts:
    # Whitelisted GitHub repository references provide context without retaining
    # prose (which can contain credentials). No relationship is inferred here.
    return [
        ("documentation.repository_reference", f"{owner}/{repository}")
        for owner, repository in re.findall(
            r"https://github\.com/([\w.-]+)/([\w.-]+)",
            source.content,
        )
    ]


class ContentCollector:
    """All content families use the same failure-isolating normalized boundary."""

    def __init__(
        self,
        name: str,
        authority: Authority,
        supports: Callable[[str], bool],
        extract: Callable[[SourceFile], Facts],
    ) -> None:
        self.name = name
        self.authority = authority
        self.supports = supports
        self.extract = extract

    def collect(self, context: Context) -> CollectionResult:
        observations: list[Observation] = []
        failures = []
        for source in context.files:
            if not self.supports(source.provenance.source):
                continue
            try:
                facts = self.extract(source)
                facts.append(("source.kind", self.name))
                observations.extend(
                    Observation(
                        self.name,
                        self.authority,
                        key,
                        value,
                        source.provenance,
                    )
                    for key, value in sorted(set(facts))
                )
            except Exception:  # A broken parser/plugin must not damage its peers.
                failures.append(Failure(self.name, source.provenance, "parse_error"))
        return CollectionResult(tuple(observations), tuple(failures))


def content_collectors() -> tuple[ContentCollector, ...]:
    return (
        ContentCollector(
            "configuration",
            Authority.CONFIGURATION,
            lambda path: (
                PurePosixPath(path).name == "Dockerfile"
                or path.endswith((".toml", ".json", ".tf"))
                or (
                    path.endswith((".yml", ".yaml"))
                    and not path.startswith(".github/workflows/")
                )
            ),
            configuration,
        ),
        ContentCollector(
            "executable",
            Authority.EXECUTABLE,
            lambda path: (
                path.startswith((".github/workflows/", "scripts/"))
                or path.endswith((".sh", ".py"))
            ),
            executable,
        ),
        ContentCollector(
            "documentation",
            Authority.DOCUMENTATION,
            lambda path: path.endswith((".md", ".rst")),
            documentation,
        ),
    )
