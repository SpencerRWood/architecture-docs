"""Consumer-to-Infisical location tables; optional metadata remains secondary."""

from dataclasses import replace

from architecture_docs.declarations import NodeKind, canonical
from architecture_docs.reconciliation import Snapshot
from architecture_docs.renderers.artifacts import (
    DEFAULT_CONFIG,
    Document,
    DocumentKind,
    RenderConfig,
    Row,
    Section,
    distinguishing_id,
)
from architecture_docs.renderers.views import View, section, sources
from architecture_docs.secret_consumers import consumer_locations
from architecture_docs.secret_mappings import mappings

MAPPING_COLUMNS = (
    "Repository/service",
    "Consumer",
    "Consumer secret name",
    "Infisical project",
    "Environment",
    "Path",
    "Infisical key",
    "Injection",
    "Required",
    "Owner",
    "Status",
    "Provider",
    "Forwarded through",
)


def render(snapshot: Snapshot, config: RenderConfig = DEFAULT_CONFIG) -> Document:
    view = View(snapshot)
    entries, unconsumed = mappings(snapshot)
    groups: dict[str, list[Row]] = {
        "mapped": [],
        "unresolved": [],
        "ambiguous": [],
        "external": [],
        "interfaces": [],
    }
    project_names = {
        loc.project: loc.project_name for entry in entries for loc, _ in entry.locations
    }
    project_names.update({loc.project: loc.project_name for loc, _ in unconsumed})
    for index, entry in enumerate(entries):
        group = (
            "interfaces"
            if entry.status == "not-forwarded"
            else entry.status
            if entry.status in {"unresolved", "ambiguous"}
            else "mapped"
            if entry.provider in {"infisical", "unknown"}
            else "external"
        )
        candidates = tuple(candidate for candidate, _ in entry.locations)
        for candidate in candidates or (None,):
            project_label = (
                candidate.project_name
                if candidate
                else project_names.get(entry.target[0], entry.target[0])
                if entry.target
                else "—"
            )
            peers = tuple(
                loc
                for loc in candidates
                if candidate
                and (loc.project_name, loc.environment, loc.path, loc.key)
                == (
                    candidate.project_name,
                    candidate.environment,
                    candidate.path,
                    candidate.key,
                )
            )
            if candidate and len(peers) > 1:
                discriminator = (
                    "project" if len({loc.project for loc in peers}) > 1 else "object"
                )
                identities = tuple(
                    loc.project if discriminator == "project" else loc.object_id
                    for loc in peers
                )
                identity = (
                    candidate.project
                    if discriminator == "project"
                    else candidate.object_id
                )
                project_label += (
                    f" ({discriminator} {distinguishing_id(identity, identities)})"
                )
            columns = (
                entry.repository,
                entry.consumer,
                entry.consumer_name,
                project_label,
                candidate.environment
                if candidate
                else entry.target[1]
                if entry.target
                else "—",
                candidate.path
                if candidate
                else entry.target[2]
                if entry.target
                else "—",
                candidate.key
                if candidate
                else entry.target[3]
                if entry.target
                else "—",
                entry.injection or "—",
                {"true": "yes", "false": "no"}.get(entry.required, "—"),
                entry.owner or "—",
                entry.status,
                entry.provider,
                entry.via or "—",
            )
            groups[group].append(
                Row(
                    f"secret-mapping:{index}:{len(groups[group])}",
                    entry.consumer_name,
                    canonical(columns),
                    entry.status,
                    sources=sources(entry.evidence),
                )
            )
    sections = tuple(
        section(f"secret-mappings:{group}", title, tuple(groups[group]), empty)
        for group, title, empty in (
            (
                "mapped",
                "Consumer-to-location mappings",
                "No uniquely verified Infisical mappings are available.",
            ),
            (
                "unresolved",
                "Unresolved repository secret references",
                "No unresolved repository secret references.",
            ),
            ("ambiguous", "Ambiguous mappings", "No ambiguous mappings."),
            (
                "external",
                "GitHub-provided secrets and reusable-workflow forwarding",
                "No GitHub secret references are evidenced.",
            ),
            (
                "interfaces",
                "Optional reusable-workflow parameters without caller bindings",
                "No unbound optional workflow parameters.",
            ),
        )
    )
    unused = tuple(
        Row(
            f"unused:{index}",
            loc.key,
            f"Infisical: {loc.project_name} / {loc.environment} / "
            f"{loc.path} / {loc.key}",
            e.verification,
            sources=sources((e,)),
        )
        for index, (loc, e) in enumerate(unconsumed)
    )
    discovery = tuple(
        replace(row, state="gap")
        for node in snapshot.graph.nodes
        for row in view.entity_rows(node, ("secret_gap",))[1:]
    )
    declarations = tuple(
        row
        for node in snapshot.graph.manifest(NodeKind.SECRET_REFERENCE)
        for row in view.entity_rows(
            node, ("project", "environment", "path", "infisical_key")
        )[1:]
        if not any(
            m.consumer_name == node.name
            and m.repository == node.repository
            and m.status in {"mapped", "stale"}
            for m in entries
        )
    )
    document = view.document(
        DocumentKind.SECRETS,
        "Secrets Manifest",
        (
            *sections,
            section(
                "consumer-locations",
                "Declared consumer secret locations",
                tuple(
                    Row(
                        f"consumer-location:{index}",
                        repo + (f" / {service}" if service else ""),
                        f"Infisical: {project_names.get(project, project)} / "
                        f"{environment} / {path}",
                        "declared" if e.verification == "verified" else "stale",
                        sources=sources((e,)),
                    )
                    for index, (
                        repo,
                        service,
                        project,
                        environment,
                        path,
                        e,
                    ) in enumerate(consumer_locations(snapshot))
                ),
                "No explicit consumer locations are declared.",
            ),
            section(
                "unconsumed",
                "Approved Infisical metadata with no known consumer",
                unused,
                "No unconsumed metadata is evidenced.",
            ),
            section(
                "discovery-gaps",
                "Discovery limitations",
                discovery,
                "No dynamic-reference discovery gaps.",
            ),
            section(
                "declared-locations",
                "Unverified repository location declarations",
                declarations,
                "No unverified location declarations.",
            ),
            Section(
                "metadata-notes",
                "Metadata notes",
                (
                    Row(
                        "metadata-notes",
                        "Interpretation",
                        "— means undeclared optional metadata. A mapping establishes "
                        "location evidence, not synchronized values or deployed "
                        "health. "
                        "Stale locations are retained and block publication. "
                        "Secret values never enter the architecture model.",
                        "metadata" if snapshot.evidence else "gap",
                    ),
                ),
            ),
        ),
        config,
    )
    count = {
        status: sum(m.status == status for m in entries)
        for status in ("mapped", "stale", "unresolved", "ambiguous")
    }
    summary = replace(
        document.sections[0].rows[0],
        value=f"{len(entries)} secret references and workflow interfaces: "
        f"{count['mapped']} mapped, "
        f"{count['stale']} stale, {count['unresolved']} unresolved, "
        f"{count['ambiguous']} ambiguous. {len(unconsumed)} approved "
        "Infisical metadata entries have no known consumer. "
        f"{sum(m.status == 'resolved' for m in entries)} GitHub references "
        "have known providers. "
        f"{sum(m.status == 'not-forwarded' for m in entries)} optional workflow "
        "parameters have no caller bindings and are interfaces, not consumer mappings. "
        f"{sum(m.status in {'location-declared', 'missing-key'} for m in entries)} "
        "Infisical references have declared locations awaiting key evidence. "
        "Unresolved means provider/location or caller cannot be determined. "
        "Declarations establish intended locations; mapped requires verified "
        "key metadata. Values are never collected.",
        state="derived" if entries or unconsumed else "gap",
    )
    return replace(
        document,
        semantic_inputs=tuple(
            sorted(
                {
                    canonical((e.observation.key, e.observation.value, e.verification))
                    for e in snapshot.evidence
                    if e.observation.key
                    in {"infisical.location", "infisical.contract", "secret.location"}
                }
            )
        ),
        sections=(
            replace(document.sections[0], rows=(summary,)),
            *document.sections[1:],
        ),
    )
