"""Approved-file metadata and evidence extraction; no discovery authorization."""

import ast
import re
from pathlib import PurePosixPath
from typing import Any

import yaml

from architecture_docs.classification import description, metadata
from architecture_docs.collectors.content import RepositoryLoader, identifiers, parsed
from architecture_docs.collectors.contracts import Context, SourceFile
from architecture_docs.collectors.github import mapping, sequence, sha
from architecture_docs.declarations import canonical, identifier
from architecture_docs.model import (
    Authority,
    CollectionResult,
    Failure,
    Observation,
    Provenance,
    SourceCoverage,
)

Facts = list[tuple[str, str]]


def relation(  # noqa: PLR0913 - typed endpoints and evidence state
    kind: str,
    source_kind: str,
    source: str,
    target_kind: str,
    target: str,
    *,
    repository: str | None = None,
    state: str = "declared",
) -> tuple[str, str]:
    target_ref = {"kind": target_kind, "name": identifier(target)}
    if repository:
        target_ref["repository"] = identifier(repository)
    return "architecture.relationship", canonical(
        {
            "kind": kind,
            "source": {"kind": source_kind, "name": identifier(source)},
            "target": target_ref,
            "state": state,
        }
    )


def metadata_facts(data: dict[str, Any], path: str) -> Facts:
    facts: Facts = []
    if path == "pyproject.toml":
        declaration = data.get("tool", {}).get("wood", {}).get("architecture")
    elif path == "architecture.toml":
        if set(data) - {
            "architecture",
            "project",
            "version",
            "nodes",
            "edges",
            "secrets",
            "secret_locations",
        }:
            raise ValueError("invalid equivalent metadata file")
        declaration = data.get("architecture")
    else:
        return facts
    if declaration is not None:
        facts.extend(
            (f"classification.{key}", value)
            for key, value in metadata(declaration).items()
        )
        facts.append(("classification.state", "declared"))
    project = data.get("project", {})
    if "name" in project:
        facts.append(("component.name", identifier(project["name"])))
    if value := description(project.get("description")):
        facts.append(("component.description", value))
    return facts


def lock_facts(data: dict[str, Any]) -> Facts:
    facts: Facts = []
    for package in data.get("package", []):
        name = identifier(package["name"])
        facts.append(("package.locked", name))
        source = package.get("source", {})
        url = source.get("git", "")
        match = re.fullmatch(
            r"https://github.com/([\w.-]+/[\w.-]+)(?:\.git)?(?:\?.*)?", url
        )
        if match:
            repository = match[1].removesuffix(".git")
            facts.append(
                (
                    "package.repository",
                    canonical({"name": name, "repository": repository}),
                )
            )
    return facts


def dbt_facts(data: dict[str, Any], path: str) -> Facts:
    facts: Facts = []
    if PurePosixPath(path).name == "manifest.json":
        nodes = data.get("nodes", {}) | data.get("sources", {})
        for unique_id, entry in sorted(nodes.items()):
            if entry.get("resource_type") not in {
                "model",
                "source",
                "seed",
                "snapshot",
            }:
                continue
            identity = identifier(unique_id)
            facts.append(("data.component", identity))
            for target in identifiers(entry.get("depends_on", {}).get("nodes", [])):
                facts.append(
                    relation("depends_on", "system", identity, "system", target)
                )
    elif "models/" in path or path.startswith("models/"):
        for source in data.get("sources", []):
            for table in source.get("tables", []):
                facts.append(
                    (
                        "data.component",
                        identifier(f"source.{source['name']}.{table['name']}"),
                    )
                )
    return facts


def deployment_facts(data: dict[str, Any], path: str) -> Facts:
    """Selectors and image pins are configured evidence, never deployment receipts."""
    facts: Facts = []
    facts.extend(
        ("environment.supported", value)
        for value in identifiers(data.get("supported_environments", []))
    )
    services = data.get("services", {})
    environment = (
        data.get("environment_name")
        if path.startswith("environments/")
        else data.get("environment")
    )
    if environment is None and path.startswith("environments/"):
        environment = PurePosixPath(path).stem
    if environment is not None:
        environment = identifier(environment)
        facts.append(("environment.configured", environment))
        for name, selected in services.items():
            if selected is True:
                facts.append(
                    (
                        "deployment.selection",
                        canonical(
                            {"environment": environment, "service": identifier(name)}
                        ),
                    )
                )
    for name, service in services.items():
        if not isinstance(service, dict):
            continue
        if image := service.get("image"):
            # Literal credentials, interpolations and arbitrary image URLs are excluded.
            if not re.fullmatch(r"[\w./-]+(?::[\w.-]+|@sha256:[a-f0-9]{64})?", image):
                continue
            facts.append(
                (
                    "deployment.image",
                    canonical({"service": identifier(name), "image": image}),
                )
            )
    return facts


def inventory_facts(data: dict[str, Any], path: str) -> Facts:
    """Inventory aliases establish configured hosts; addresses/vars are excluded."""
    match = re.fullmatch(r"ansible/inventory/([\w.-]+)/hosts\.ya?ml", path)
    if not match:
        return []
    environment = identifier(match[1])
    facts: Facts = [("environment.configured", environment)]

    def hosts(group: dict[str, Any]) -> set[str]:
        names = set(identifiers(group.get("hosts", {})))
        for child in mapping(group.get("children", {})).values():
            names.update(hosts(mapping(child)))
        return names

    for name in sorted(hosts(mapping(data.get("all", {})))):
        facts.append(("architecture.node", canonical({"kind": "host", "name": name})))
        facts.append(relation("hosted_by", "environment", environment, "host", name))
    return facts


def deployment_receipts(context: Context, source: SourceFile) -> CollectionResult:
    """Approved selectors query GitHub Deployments, not application capabilities.

    A success status attests deployment at a revision; it does not attest image
    digest, selected services, runtime health or a later live state.
    """
    data = parsed(source)
    if (
        set(data) != {"deployments"}
        or not isinstance(data["deployments"], list)
        or len(data["deployments"]) > 50
    ):
        raise ValueError("invalid deployment receipt selectors")
    observations: list[Observation] = []
    failures: list[Failure] = []
    coverage: list[SourceCoverage] = []
    for selector in data["deployments"]:
        if (
            not isinstance(selector, dict)
            or set(selector) != {"id", "service"}
            or type(selector["id"]) is not int
            or selector["id"] <= 0
        ):
            raise ValueError("invalid deployment receipt selector")
        service = identifier(selector["service"])
        endpoint = f"/repos/{context.repository}/deployments/{selector['id']}"
        provenance = Provenance(
            context.repository, f"github:deployments/{selector['id']}", None
        )
        try:
            deployment = mapping(context.github.get(endpoint))
            if mapping(deployment.get("payload", {})).get("service") != service:
                raise ValueError("deployment service unverified")
            statuses = sequence(context.github.get(endpoint + "/statuses?per_page=1"))
            revision = sha(deployment.get("sha"))
            environment = identifier(deployment["environment"])
            provenance = Provenance(context.repository, provenance.source, revision)
            if not statuses or mapping(statuses[0]).get("state") != "success":
                raise ValueError("deployment unverified")
            facts = {
                "service": service,
                "environment": environment,
                "revision": revision,
            }
            # Each source selector participates in evidence: dropping the selector
            # cannot silently delete a previously observed deployment.
            observations.extend(
                (
                    Observation(
                        ArchitectureCollector.name,
                        Authority.GITHUB,
                        "deployment.attested",
                        canonical(facts),
                        provenance,
                    ),
                    Observation(
                        ArchitectureCollector.name,
                        Authority.CONFIGURATION,
                        "deployment.receipt_selector",
                        str(selector["id"]),
                        source.provenance,
                    ),
                )
            )
            coverage.append(
                SourceCoverage(
                    context.repository,
                    provenance.source,
                    ArchitectureCollector.name,
                    revision,
                )
            )
        except Exception:
            failures.append(
                Failure(
                    ArchitectureCollector.name,
                    provenance,
                    "deployment_evidence_unavailable",
                )
            )
    coverage.append(
        SourceCoverage(
            context.repository,
            source.provenance.source,
            ArchitectureCollector.name,
            context.revision,
        )
    )
    return CollectionResult(
        tuple(observations), tuple(failures), coverage=tuple(coverage)
    )


def workspace_facts(data: dict[str, Any]) -> Facts:
    facts: Facts = []
    for location in data.get("load_from", []):
        for field in ("python_module", "python_package", "python_file"):
            selected = location.get(field)
            if selected is None:
                continue
            name = (
                selected
                if isinstance(selected, str)
                else selected.get(
                    "module_name",
                    selected.get("package_name", selected.get("relative_path")),
                )
            )
            facts.append(("dagster.location", identifier(name)))
    return facts


def infrastructure_facts(data: dict[str, Any]) -> Facts:
    """Existing runtime-service contracts supply explicit Compose mappings."""
    facts: Facts = []
    for settings in data.get("infrastructure_infisical_runtime_services", {}).values():
        path = identifier(settings["compose_file"])
        facts.append(("orchestration.component", path))
        for service in identifiers(settings.get("compose_services", [])):
            facts.append(
                relation("orchestrates", "orchestration", path, "service", service)
            )
    return facts


def integration_facts(data: dict[str, Any], repository: str) -> Facts:
    facts: Facts = []
    for integration in data.get("integrations", []):
        # Only explicit repository identifiers create cross-repository edges.
        target = integration.get("repository")
        if not isinstance(target, str) or not re.fullmatch(r"[\w.-]+/[\w.-]+", target):
            continue
        kind = integration.get("kind", "depends_on")
        if kind not in {"depends_on", "notifies", "orchestrates"}:
            raise ValueError("invalid integration kind")
        facts.append(
            relation(
                kind, "repository", repository, "repository", target, repository=target
            )
        )
    return facts


def python_facts(source: SourceFile) -> Facts:
    tree = ast.parse(source.content)
    facts: Facts = []
    imports = {
        alias.asname or alias.name: alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "dagster"
        for alias in node.names
    }
    modules = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
        if alias.name == "dagster"
    }
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = (
            imports.get(node.func.id, "")
            if isinstance(node.func, ast.Name)
            else node.func.attr
            if isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in modules
            else ""
        )
        if name in {"Definitions", "define_asset_job"}:
            facts.append(
                ("orchestration.component", identifier(source.provenance.source))
            )
        if name in {"AssetIn", "AssetKey"}:
            values = [
                *node.args,
                *(keyword.value for keyword in node.keywords if keyword.arg == "key"),
            ]
            for value in values:
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    facts.append(
                        relation(
                            "depends_on",
                            "orchestration",
                            source.provenance.source,
                            "system",
                            identifier(value.value),
                        )
                    )
    return facts


def sql_facts(source: SourceFile) -> Facts:
    path = source.provenance.source
    if not path.startswith("models/"):
        return []
    identity = identifier(PurePosixPath(path).stem)
    # Strip SQL/Jinja comments so a commented ref is not architecture evidence.
    text = re.sub(r"/\*.*?\*/|\{#.*?#\}|--[^\n]*", "", source.content, flags=re.S)
    facts: Facts = [("data.component", identity)]
    for macro, first, second in re.findall(
        r"\b(ref|source)\(\s*['\"]([\w.-]+)['\"]\s*(?:,\s*['\"]([\w.-]+)['\"]\s*)?\)",
        text,
    ):
        target = f"source.{first}.{second}" if macro == "source" else first
        facts.append(relation("depends_on", "system", identity, "system", target))
    return facts


def extract(source: SourceFile) -> Facts:
    path = source.provenance.source
    if path.endswith(".py"):
        return python_facts(source)
    if path.endswith(".sql"):
        return sql_facts(source)
    data = parsed(source)
    facts = metadata_facts(data, path)
    if PurePosixPath(path).name in {"uv.lock", "poetry.lock"}:
        facts += lock_facts(data)
    facts += dbt_facts(data, path)
    facts += workspace_facts(data)
    facts += infrastructure_facts(data)
    facts += deployment_facts(data, path)
    facts += inventory_facts(data, path)
    facts += integration_facts(data, source.provenance.repository)
    if path == ".github/release.toml":
        for settings in data.values():
            if isinstance(settings, dict):
                for field in ("workflow", "promotion_workflow"):
                    for target in identifiers([settings.get(field)]):
                        facts.append(("workflow.uses", target))
    return facts


class ArchitectureCollector:
    name = "architecture_evidence"

    def collect(self, context: Context) -> CollectionResult:
        observations: list[Observation] = []
        failures: list[Failure] = []
        coverage: list[SourceCoverage] = []
        for source in context.files:
            path = source.provenance.source
            if not path.endswith(
                (".toml", ".json", ".yaml", ".yml", ".py", ".sql", ".lock")
            ) or path.startswith(".github/workflows/"):
                continue
            try:
                # YAML document markers do not imply a sequence. Parse the root
                # type so `---` environment/Compose manifests remain evidence.
                if path.endswith((".yaml", ".yml")) and isinstance(
                    yaml.load(source.content, Loader=RepositoryLoader),  # noqa: S506
                    list,
                ):
                    match = re.fullmatch(
                        r"(?:ansible/)?roles/([\w.-]+)/tasks/main\.ya?ml", path
                    )
                    if match:
                        observations.append(
                            Observation(
                                self.name,
                                Authority.CONFIGURATION,
                                "orchestration.component",
                                f"role:{match[1]}",
                                source.provenance,
                            )
                        )
                    coverage.append(
                        SourceCoverage(
                            context.repository, path, self.name, context.revision
                        )
                    )
                    continue
                if path == "deployment-receipts.json":
                    result = deployment_receipts(context, source)
                    observations.extend(result.observations)
                    coverage.extend(result.coverage)
                    failures.extend(result.failures)
                    continue
                observations.extend(
                    Observation(
                        self.name,
                        Authority.CONFIGURATION,
                        key,
                        value,
                        source.provenance,
                    )
                    for key, value in sorted(set(extract(source)))
                )
                coverage.append(
                    SourceCoverage(
                        context.repository, path, self.name, context.revision
                    )
                )
            except Exception:
                failures.append(Failure(self.name, source.provenance, "parse_error"))
        return CollectionResult(
            tuple(observations), tuple(failures), coverage=tuple(coverage)
        )
