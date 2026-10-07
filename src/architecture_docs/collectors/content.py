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
from architecture_docs.collectors.workflow_secrets import secret_facts
from architecture_docs.declarations import (
    architecture_declarations,
    canonical,
    node_declaration,
)
from architecture_docs.model import (
    Authority,
    CollectionResult,
    Failure,
    Observation,
    SourceCoverage,
)

Facts = list[tuple[str, str]]


class RepositoryLoader(yaml.SafeLoader):
    """Use YAML 1.2 booleans; GitHub Actions' `on` is a literal mapping key."""


RepositoryLoader.yaml_implicit_resolvers = {
    key: [
        (tag, pattern) for tag, pattern in resolvers if tag != "tag:yaml.org,2002:bool"
    ]
    for key, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
RepositoryLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
    list("tTfF"),
)


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
    # The subclass retains SafeLoader's constructors; only scalar resolution changes.
    return mapping(yaml.load(source.content, Loader=RepositoryLoader))  # noqa: S506


def configuration(source: SourceFile) -> Facts:
    path = source.provenance.source
    if PurePosixPath(path).name == "architecture.toml":
        return architecture_declarations(parsed(source))
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
    services = compose_services(data, path)
    for section in ("volumes", "networks", "jobs"):
        facts.extend(
            (f"compose.{section}", value)
            for value in identifiers(
                data.get(section, {}),
            )
        )
    facts.extend(("compose.services", name) for name in identifiers(services))
    facts.extend(postgres_facts(data))
    for name, service in services.items():
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
        volumes = settings.get("volumes", [])
        facts.extend(
            (f"service.{name}.volume", value.split(":", 1)[0])
            for value in identifiers(volumes)
            if value.split(":", 1)[0] in identifiers(data.get("volumes", {}))
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


def compose_services(data: dict[str, Any], path: str) -> dict[str, Any]:
    """Environment boolean selectors cannot establish Compose service topology."""
    services = mapping(data.get("services", {}))
    if all(isinstance(value, dict) for value in services.values()):
        return services
    if "compose" in PurePosixPath(path).name or not all(
        type(value) is bool for value in services.values()
    ):
        raise ValueError("invalid compose services")
    return {}


def postgres_facts(data: dict[str, Any]) -> Facts:
    """Retain database/role identities without connection or credential values."""
    applications = data.get("postgres_applications", [])
    if not isinstance(applications, list):
        raise ValueError("invalid postgres applications")
    facts: Facts = []
    for application in applications:
        settings = mapping(application)
        attributes = {"technology": "postgresql"}
        if "role" in settings:
            attributes["role"] = settings["role"]
        declaration = node_declaration(
            {
                "kind": "database",
                "name": settings["database"],
                "attributes": attributes,
            }
        )
        facts.append(("architecture.node", canonical(declaration)))
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
            facts.extend(shell_facts(source.content))
        return facts
    data = parsed(source)
    facts = secret_facts(data)
    for name, job in mapping(data.get("jobs", {})).items():
        settings = mapping(job)
        encoded = json.dumps(settings)
        facts.extend(("workflow.job", value) for value in identifiers([name]))
        actions = [settings.get("uses")]
        actions.extend(mapping(step).get("uses") for step in settings.get("steps", []))
        for action in identifiers(actions):
            # Only literal action/reusable-workflow references, no expressions.
            if "@" in action or action.startswith("./"):
                facts.append(("workflow.uses", action))
        facts.extend(
            ("workflow.secret_name", value)
            for value in sorted(
                set(
                    re.findall(
                        r"\$\{\{\s*secrets\.([A-Za-z_][A-Za-z0-9_]*)\s*\}\}",
                        encoded,
                    )
                )
            )
        )
        if re.search(r"secrets\s*\[", encoded):
            facts.append(("workflow.secret_gap", "indexed_secret_reference_unresolved"))
        if any(
            isinstance(value, str) and "${{" not in value
            for value in (
                {}
                if settings.get("secrets") == "inherit"
                else mapping(settings.get("secrets", {}))
            ).values()
        ):
            facts.append(("workflow.secret_gap", "literal_secret_input_excluded"))
    return facts


def shell_facts(content: str) -> Facts:
    """Read logical shell lines; retain only known command names, never arguments."""
    commands = {
        "ansible",
        "ansible-playbook",
        "bash",
        "curl",
        "dbt",
        "docker",
        "gh",
        "infisical",
        "psql",
        "python",
        "python3",
        "sh",
        "ssh",
        "terraform",
        "uv",
        "wood",
    }
    facts: Facts = []
    pending = ""
    delimiter: str | None = None
    for line in content.replace("\\\n", "").splitlines():
        if delimiter is not None:
            if line.lstrip("\t") == delimiter:
                delimiter = None
            continue
        pending += line + "\n"
        try:
            tokens = shlex.split(pending, comments=True)
        except ValueError:
            continue  # A quoted string can span logical lines.
        if match := re.search(r"<<-?\s*(?:'([\w]+)'|\"([\w]+)\"|([\w]+))", pending):
            delimiter = next(value for value in match.groups() if value is not None)
        pending = ""
        if tokens and tokens[0] == "exec":
            tokens = tokens[1:]
        if tokens and PurePosixPath(tokens[0]).name in commands:
            facts.append(("shell.executable", PurePosixPath(tokens[0]).name))
    if pending or delimiter is not None:
        raise ValueError("incomplete shell structure")
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
        coverage = []
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
                coverage.append(
                    SourceCoverage(
                        context.repository,
                        source.provenance.source,
                        self.name,
                        context.revision,
                    )
                )
            except Exception:  # A broken parser/plugin must not damage its peers.
                failures.append(Failure(self.name, source.provenance, "parse_error"))
        return CollectionResult(
            tuple(observations), tuple(failures), coverage=tuple(coverage)
        )


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
