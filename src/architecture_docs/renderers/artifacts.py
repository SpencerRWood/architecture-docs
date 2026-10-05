"""Versioned deterministic document artifacts, independent of Drive and Codex."""

import html
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
        return cls(digest(fields), *fields)


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
    renderer_version: int = 1
    schema_version: int = 1

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
        lines = [
            f"# {escape(self.title)}",
            "",
            f"Deterministic snapshot: `{self.snapshot_id}`.",
            "",
            f"Generation state: **{self.generation_state}**. "
            f"Publication blocked: **{str(self.publication_blocked).lower()}**.",
            "",
            f"Model {self.model_version}; graph {self.graph_version}; "
            f"renderer {self.renderer_version}; artifact {self.schema_version}.",
            "",
            "Declarations describe repository intent; "
            "they do not attest deployed health.",
        ]
        for section in self.sections:
            lines.extend(["", f"## {escape(section.title)}", ""])
            if section.diagram:
                lines.extend(["```mermaid", section.diagram, "```", ""])
            lines.extend(
                [
                    "| Item | Deterministic evidence | Evidence state | Sources |",
                    "| --- | --- | --- | --- |",
                ]
            )
            for row in section.rows:
                sources = " ".join(f"[^s-{source.id}]" for source in row.sources) or "—"
                lines.append(
                    f"| {escape(row.label)} | {escape(row.value)} | "
                    f"{escape(row.state)} | {sources} |"
                )
        lines.extend(["", "## Source provenance", ""])
        for source in self.sources:
            label = escape(f"{source.repository} / {source.source}")
            location = source_url(source) if config.include_source_links else None
            label = f"[{label}]({location})" if location else label
            lines.append(
                f"[^s-{source.id}]: {label}; revision "
                f"{escape(source.revision or 'unavailable')}; blob "
                f"{escape(source.blob or 'unavailable')}; "
                f"{escape(source.collector)}; {source.authority}; "
                f"{source.verification}."
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


def source_url(source: SourceReference) -> str:
    # Exact pinned links when available; metadata remains an explicitly named object.
    repository = quote(source.repository, safe="/")
    if source.source.startswith("github:"):
        return f"https://github.com/{repository}"
    if source.revision:
        revision = quote(source.revision, safe="")
        path = quote(source.source, safe="/")
        return f"https://github.com/{repository}/blob/{revision}/{path}"
    return f"https://github.com/{repository}"
