"""Named repository scopes and reusable-workflow caller chains, without values."""

import json
import re
from typing import TYPE_CHECKING

from architecture_docs.graph import Evidence, Node, ordered
from architecture_docs.reconciliation import Snapshot
from architecture_docs.secret_locations import SecretLocation

if TYPE_CHECKING:
    from architecture_docs.secret_mappings import SecretMapping

Origin = tuple[str, str, str, str, tuple[Evidence, ...]]


def consumer_locations(
    snapshot: Snapshot,
) -> tuple[tuple[str, str, str, str, str, Evidence], ...]:
    result = []
    explicit = {
        (
            e.observation.provenance.repository,
            SecretLocation(**json.loads(e.observation.value)).service,
        )
        for e in snapshot.evidence
        if e.observation.key == "secret.location"
    }
    for e in snapshot.evidence:
        if e.observation.key == "secret.location":
            d = SecretLocation(**json.loads(e.observation.value))
            result.append(
                (
                    e.observation.provenance.repository,
                    d.service,
                    d.project,
                    d.environment,
                    d.path,
                    e,
                )
            )
        elif e.observation.key == "infisical.contract":
            for b in json.loads(e.observation.value)["bindings"]:
                if not b["consumer"] and (b["repository"], "") not in explicit:
                    result.append(
                        (
                            b["repository"],
                            "",
                            b["project"],
                            b["environment"],
                            b["path"],
                            e,
                        )
                    )
    return tuple(sorted(result, key=lambda row: row[:5]))


def declared_targets(
    snapshot: Snapshot, repository: str, service: str = ""
) -> tuple[set[tuple[str, str, str]], tuple[Evidence, ...]]:
    declarations = tuple(
        (SecretLocation(**json.loads(e.observation.value)), e)
        for e in snapshot.evidence
        if e.observation.key == "secret.location"
        and e.observation.provenance.repository == repository
    )
    selected = tuple((d, e) for d, e in declarations if d.service == service)
    return {(d.project, d.environment, d.path) for d, _ in selected}, ordered(
        [e for _, e in selected]
    )


def origins(
    snapshot: Snapshot,
    repository: str,
    path: str,
    name: str,
    visited: frozenset[tuple[str, str, str]] = frozenset(),
) -> tuple[Origin, ...]:
    identity = (repository, path, name)
    if identity in visited:
        return ((repository, path, name, "unresolved", ()),)
    reusable = tuple(
        e
        for e in snapshot.evidence
        if e.observation.key == "workflow.reusable"
        and (e.observation.provenance.repository, e.observation.provenance.source)
        == (repository, path)
    )
    if not reusable:
        return (
            (
                repository,
                path,
                name,
                "unresolved" if name.startswith("input:") else "resolved",
                (),
            ),
        )
    result = []
    for e in snapshot.evidence:
        if e.observation.key != "workflow.call":
            continue
        call = json.loads(e.observation.value)
        provenance = e.observation.provenance
        target, _, ref = call["target"].partition("@")
        if target.startswith("./"):
            destination = (provenance.repository, target.removeprefix("./"))
        else:
            parts = target.split("/.github/", 1)
            destination = (
                (parts[0], ".github/" + parts[1]) if len(parts) == 2 else ("", "")
            )
        if destination != (repository, path):
            continue
        if re.fullmatch(r"[a-f0-9]{40}", ref) and any(
            item.observation.provenance.revision != ref for item in reusable
        ):
            continue
        passed = (
            call["inputs"].get(name.removeprefix("input:"), "")
            if name.startswith("input:")
            else call["secrets"].get(name)
            or (name if call["inherit"] or name.upper() == "GITHUB_TOKEN" else "")
        )
        if not passed:
            continue
        for repo, source, secret, state, chain in origins(
            snapshot,
            provenance.repository,
            provenance.source,
            passed,
            visited | {identity},
        ):
            result.append(
                (repo, source, secret, state, ordered([e, *reusable, *chain]))
            )
    optional = tuple(
        e
        for e in snapshot.evidence
        if e.observation.key == "workflow.secret_parameter"
        and (e.observation.provenance.repository, e.observation.provenance.source)
        == (repository, path)
        and json.loads(e.observation.value) == {"name": name, "required": False}
    )
    return tuple(result) or (
        (
            repository,
            path,
            name,
            "resolved"
            if name.upper() == "GITHUB_TOKEN"
            else "not-forwarded"
            if optional
            else "unresolved",
            ordered([*reusable, *optional]),
        ),
    )


def workflow_mappings(
    snapshot: Snapshot, node: Node, path: str
) -> tuple[SecretMapping, ...]:
    from architecture_docs.secret_mappings import SecretMapping  # noqa: PLC0415

    result = []
    for repo, consumer, name, state, chain in origins(
        snapshot, node.repository, path, node.name
    ):
        provider = (
            "github-provided"
            if name.upper() == "GITHUB_TOKEN"
            else "github-repository-or-organization"
            if state == "resolved"
            else "reusable-workflow-input"
        )
        evidence = ordered(
            [
                *(e for e in node.evidence if e.observation.provenance.source == path),
                *chain,
            ]
        )
        status = (
            "stale"
            if state == "resolved" and any(e.verification == "stale" for e in evidence)
            else state
        )
        via = (
            f"{node.repository}/{path} / {node.name}"
            if (repo, consumer, name) != (node.repository, path, node.name)
            else ""
        )
        result.append(
            SecretMapping(
                repo,
                consumer,
                name,
                (),
                "reusable-workflow secret forwarding" if chain else "github-actions",
                "",
                "",
                status,
                evidence,
                provider,
                (),
                via,
            )
        )
    return tuple(result)
