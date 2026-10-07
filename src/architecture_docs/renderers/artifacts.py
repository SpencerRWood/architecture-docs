"""Versioned deterministic document artifacts, independent of Drive and Codex."""

import html
import json
import re
from dataclasses import asdict, dataclass
from enum import StrEnum
from hashlib import sha256
from urllib.parse import quote

from architecture_docs.declarations import canonical
from architecture_docs.graph import Evidence


class DocumentKind(StrEnum):
    OVERVIEW = "architecture-overview"
    CATALOG = "repository-dependency-catalog"
    DEPLOYMENT = "deployment-release-architecture"
    RUNTIME = "runtime-infrastructure-architecture"
    DATA = "data-storage-architecture"
    AUTOMATION = "automation-orchestration-architecture"
    SECRETS = "secrets-manifest"
    DEPLOYMENT_RUNBOOK = "runbook-deployment-redeployment"
    ROLLBACK_RUNBOOK = "runbook-rollback-known-good-recovery"
    HOST_RUNBOOK = "runbook-linux-host-rebuild-recovery"
    SECRETS_RUNBOOK = "runbook-secret-reference-management"
    DATABASE_RUNBOOK = "runbook-database-provisioning-onboarding"
    RELEASE_RUNBOOK = "runbook-release-promotion-troubleshooting"
    RESTORATION_RUNBOOK = "runbook-service-restoration"
    DAGSTER_RUNBOOK = "runbook-dagster-operations"


@dataclass(frozen=True)
class RenderConfig:
    title_prefix: str = ""
    include_source_links: bool = True

    def __post_init__(self) -> None:
        if (
            not isinstance(self.title_prefix, str)
            or len(self.title_prefix) > 100
            or any(ord(char) < 32 for char in self.title_prefix)
        ):
            raise ValueError("invalid document title prefix")
        if type(self.include_source_links) is not bool:
            raise ValueError("invalid source-link configuration")

    @property
    def id(self) -> str:
        return digest(asdict(self))


DEFAULT_CONFIG = RenderConfig()


def digest(data: object) -> str:
    return sha256(canonical(data).encode()).hexdigest()


@dataclass(frozen=True, order=True)
class SourceReference:
    id: str
    repository: str
    source: str
    revision: str | None
    blob: str | None
    collector: str
    authority: str
    verification: str
    display_name: str = ""

    @classmethod
    def from_evidence(cls, item: Evidence) -> SourceReference:
        observation = item.observation
        provenance = observation.provenance
        fields = (
            provenance.repository,
            provenance.source,
            provenance.revision,
            provenance.blob,
            observation.collector,
            observation.authority.name.lower(),
            item.verification,
        )
        display_name = ""
        if observation.key == "infisical.location":
            location = json.loads(observation.value)
            display_name = (
                f"Infisical: {location['project_name']} / {location['environment']} / "
                f"{location['path']} / {location['key']}"
            )
        elif observation.key == "infisical.contract":
            display_name = "Infisical: approved collection scope"
        elif observation.key == "github.path":
            display_name = (
                f"GitHub: {provenance.repository}/{observation.value} "
                "(workflow metadata)"
            )
        elif observation.key == "github.tag_name":
            display_name = (
                f"GitHub: {provenance.repository} / release {observation.value}"
            )
        return cls(digest(fields), *fields, display_name)

    @property
    def human_label(self) -> str:
        if self.display_name:
            return self.display_name
        if self.source.startswith("worktree:"):
            path = self.source.removeprefix("worktree:")
            return f"Local worktree: {self.repository}/{path}"
        if self.collector == "estate":
            return f"Configuration: {self.repository}/{self.source}"
        if self.source.startswith("github:"):
            endpoint = self.source.removeprefix("github:")
            if endpoint.rsplit("/", 1)[-1].isdigit():
                endpoint = endpoint.rsplit("/", 1)[0]
            return f"GitHub: {self.repository} / {endpoint} metadata"
        if self.source.startswith("infisical:"):
            # Project and provider IDs stay in the source reference, not its label.
            scope = self.source.removeprefix("infisical:").split("/", 1)
            return "Infisical: " + (scope[1] if len(scope) == 2 else "metadata scope")
        return f"GitHub: {self.repository}/{self.source}"


@dataclass(frozen=True)
class Row:
    id: str
    label: str
    value: str
    state: str
    entities: tuple[str, ...] = ()
    sources: tuple[SourceReference, ...] = ()


@dataclass(frozen=True)
class Section:
    id: str
    title: str
    rows: tuple[Row, ...]
    diagram: str | None = None


@dataclass(frozen=True)
class Document:
    id: DocumentKind
    title: str
    snapshot_id: str
    model_version: int
    graph_version: int
    configuration_id: str
    sections: tuple[Section, ...]
    publication_blocked: bool
    generation_state: str
    renderer_version: int = 2
    schema_version: int = 1
    semantic_inputs: tuple[str, ...] = ()

    def to_json(self) -> str:
        return canonical(self.to_data())

    def to_data(self) -> dict[str, object]:
        return {**asdict(self), "content_hash": self.content_hash}

    @property
    def content_hash(self) -> str:
        """Semantic document content excludes changing commit/blob citations."""
        return digest(
            {
                "id": self.id,
                "title": self.title,
                "generation_state": self.generation_state,
                "renderer_version": self.renderer_version,
                **(
                    {"semantic_inputs": self.semantic_inputs}
                    if self.semantic_inputs
                    else {}
                ),
                "sections": [
                    {
                        "id": section.id,
                        "title": section.title,
                        "diagram": section.diagram,
                        "rows": [
                            {
                                key: value
                                for key, value in asdict(row).items()
                                if key != "sources"
                            }
                            for row in section.rows
                        ],
                    }
                    for section in self.sections
                ],
            }
        )

    @property
    def sources(self) -> tuple[SourceReference, ...]:
        return tuple(
            sorted(
                {
                    source
                    for section in self.sections
                    for row in section.rows
                    for source in row.sources
                }
            )
        )

    def to_markdown(self, config: RenderConfig = DEFAULT_CONFIG) -> str:
        if config.id != self.configuration_id:
            raise ValueError("document rendering configuration mismatch")
        references = tuple(
            sorted(self.sources, key=lambda s: (s.human_label, s.verification, s))
        )
        citations = {source: str(index) for index, source in enumerate(references, 1)}

        def cite(row: Row) -> str:
            return " ".join(f"[^{citations[s]}]" for s in row.sources) or "—"

        def display(value: str, row: Row) -> str:
            diagnostic = any(
                state in row.state for state in ("stale", "ambiguous", "drift")
            )
            return escape(human_text(value, diagnostic=diagnostic))

        lines = [
            f"# {escape(self.title)}",
            "",
            f"Generation state: **{self.generation_state}**. "
            f"Publication blocked: **{str(self.publication_blocked).lower()}**.",
            "",
            "Declarations describe repository intent; "
            "they do not attest deployed health.",
        ]
        for section in self.sections:
            lines.extend(["", f"## {escape(human_text(section.title))}", ""])
            if section.id.startswith("secret-mappings:") and any(
                row.id.startswith("secret-mapping:") for row in section.rows
            ):
                from architecture_docs.renderers.secrets import (  # noqa: PLC0415
                    MAPPING_COLUMNS,
                )

                lines.append("| " + " | ".join(MAPPING_COLUMNS) + " | Sources |")
                lines.append(
                    "| "
                    + " | ".join("---" for _ in (*MAPPING_COLUMNS, "Sources"))
                    + " |"
                )
                for row in section.rows:
                    columns = json.loads(row.value)
                    lines.append(
                        "| "
                        + " | ".join(display(c, row) for c in columns)
                        + f" | {cite(row)} |"
                    )
                continue
            if section.diagram:
                lines.extend(
                    [
                        "```mermaid",
                        human_text(
                            section.diagram, diagnostic=self.publication_blocked
                        ),
                        "```",
                        "",
                    ]
                )
            lines.extend(
                [
                    "| Item | Deterministic evidence | Evidence state | Sources |",
                    "| --- | --- | --- | --- |",
                ]
            )
            for row in section.rows:
                lines.append(
                    f"| {display(row.label, row)} | {display(row.value, row)} | "
                    f"{display(row.state, row)} | {cite(row)} |"
                )
        lines.extend(["", "## Source provenance", ""])
        labels = [source.human_label for source in references]
        for source in references:
            label = escape(human_text(source.human_label))
            location = source_url(source) if config.include_source_links else None
            label = f"[{label}]({location})" if location else label
            context = ""
            if (
                source.revision
                and re.fullmatch(r"[a-fA-F0-9]{40,64}", source.revision)
                and (
                    source.verification == "stale"
                    or labels.count(source.human_label) > 1
                )
            ):
                context = f"; commit {source.revision[:12]}"
            if source.source.startswith("worktree:"):
                context += "; uncommitted"
            lines.append(
                f"[^{citations[source]}]: {label}; "
                f"{escape(source.verification)}{context}."
            )
        if not self.sources:
            lines.append("No source facts are available in this view.")
        return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class DocumentSet:
    snapshot_id: str
    documents: tuple[Document, ...]
    schema_version: int = 1

    def to_json(self) -> str:
        return canonical(
            {
                "snapshot_id": self.snapshot_id,
                "schema_version": self.schema_version,
                "documents": [doc.to_data() for doc in self.documents],
            }
        )


def escape(value: str) -> str:
    text = html.escape(value, quote=False).replace("\n", " ").replace("\r", " ")
    return re.sub(r"([\\`*_\[\]{}()#!|~])", r"\\\1", text)


def human_text(value: str, *, diagnostic: bool = False) -> str:
    """Project incidental identifiers without altering internal facts or hashes."""
    # A pinned workflow/action ref is useful context, but needs only a short commit.
    value = re.sub(r"(?<=@)[a-fA-F0-9]{40,64}\b", lambda m: m[0][:12], value)
    value = re.sub(
        r"(?i)\b((?:(?:file|object|source|snapshot|project|workspace|graph|node|drive)"
        r"[ _-](?:id|uuid|hash)|blob)\s*[:=]\s*)"
        r"([A-Za-z0-9_-]{20,})\b",
        lambda m: m[1] + (m[2][:8] if diagnostic else "identifier retained internally"),
        value,
    )
    uuid_pattern = r"\b[a-fA-F0-9]{8}(?:-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12}\b"
    identities = tuple(re.findall(uuid_pattern, value))
    value = re.sub(
        uuid_pattern,
        lambda m: (
            distinguishing_id(m[0], identities)
            if diagnostic
            else "identifier retained internally"
        ),
        value,
    )
    return re.sub(
        r"\b(?:metadata-sha256:|sha256:)?[a-fA-F0-9]{32,}\b",
        lambda m: m[0][:12] if diagnostic else "identifier retained internally",
        value,
    )


def distinguishing_id(value: str, others: tuple[str, ...]) -> str:
    """Use only enough characters to distinguish an otherwise identical candidate."""
    width = 8
    while any(other != value and other[:width] == value[:width] for other in others):
        width += 4
    return value[:width]


def source_url(source: SourceReference) -> str:
    # Exact pinned links when available; metadata remains an explicitly named object.
    repository = quote(source.repository, safe="/")
    if source.source.startswith(("infisical:", "worktree:")):
        return ""
    if source.source.startswith("github:"):
        return f"https://github.com/{repository}"
    if human_text(source.source) != source.source:
        return f"https://github.com/{repository}"
    if source.revision:
        revision = quote(human_text("@" + source.revision)[1:], safe="")
        path = quote(source.source, safe="/")
        return f"https://github.com/{repository}/blob/{revision}/{path}"
    return f"https://github.com/{repository}"
