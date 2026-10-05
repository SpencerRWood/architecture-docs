"""Pin reads, isolate source failures, and dispatch the common plugin interface."""

from architecture_docs.collectors.content import content_collectors
from architecture_docs.collectors.contracts import Collector, Context, SourceFile
from architecture_docs.collectors.github import (
    GitHub,
    SourceError,
    encoded_ref,
    mapping,
    sequence,
    sha,
    text,
)
from architecture_docs.collectors.metadata import MetadataCollector
from architecture_docs.config import Registry, Repository
from architecture_docs.model import (
    CollectionResult,
    Failure,
    Observation,
    Provenance,
    RepositoryInventory,
    SourceCoverage,
    precedence,
)


def source_files(
    github: GitHub,
    repository: Repository,
    registry: Registry,
    revision: str,
    tree_sha: str,
) -> tuple[tuple[SourceFile, ...], tuple[Failure, ...], RepositoryInventory]:
    response = mapping(
        github.get(
            f"/repos/{repository.name}/git/trees/{tree_sha}?recursive=1",
        )
    )
    if response.get("truncated") is not False:
        raise SourceError("incomplete_tree")
    entries = sequence(response.get("tree"))
    approved = []
    for entry in entries:
        data = mapping(entry)
        path = text(data.get("path"))
        if repository.approves(path) and data.get("type") == "blob":
            approved.append(data)
    if len(approved) > registry.max_files:
        raise SourceError("file_count_limit")
    files = []
    failures = []
    for entry in sorted(approved, key=lambda item: item["path"]):
        provenance = Provenance(repository.name, entry["path"], revision)
        try:
            blob_sha = sha(entry.get("sha"))
            provenance = Provenance(repository.name, entry["path"], revision, blob_sha)
            if entry.get("mode") not in ("100644", "100755"):
                raise SourceError("unsupported_file_mode")
            size = entry.get("size")
            if not isinstance(size, int) or size < 0:
                raise SourceError("malformed_response")
            if size > registry.max_file_bytes:
                raise SourceError("file_too_large")
            files.append(
                SourceFile(
                    provenance,
                    github.blob(
                        repository.name,
                        blob_sha,
                        registry.max_file_bytes,
                    ),
                )
            )
        except SourceError as error:
            failures.append(Failure("github_content", provenance, str(error)))
    return (
        tuple(files),
        tuple(failures),
        RepositoryInventory(
            repository.name,
            revision,
            tuple(sorted(entry["path"] for entry in approved)),
            repository.paths,
        ),
    )


def collect(
    registry: Registry,
    github: GitHub,
    collectors: tuple[Collector, ...] | None = None,
) -> CollectionResult:
    plugins: tuple[Collector, ...] = (
        (*content_collectors(), MetadataCollector())
        if collectors is None
        else collectors
    )
    names = [plugin.name for plugin in plugins]
    if len(names) != len(set(names)):
        raise ValueError("duplicate collector identity")
    observations: list[Observation] = []
    failures: list[Failure] = []
    skipped = []
    coverage: list[SourceCoverage] = []
    inventories = []
    for repository in sorted(registry.repositories, key=lambda item: item.name):
        provenance = Provenance(repository.name, "github:repository", None)
        try:
            metadata = mapping(github.get(f"/repos/{repository.name}"))
            archived = metadata.get("archived")
            if not isinstance(archived, bool):
                raise SourceError("malformed_response")
            if (repository.archived == "exclude" and archived) or (
                repository.archived == "only" and not archived
            ):
                skipped.append(repository.name)
                continue
            ref = repository.ref or text(metadata.get("default_branch"))
            commit = mapping(
                github.get(
                    f"/repos/{repository.name}/commits/{encoded_ref(ref)}",
                )
            )
            revision = sha(commit.get("sha"))
            provenance = Provenance(repository.name, "github:tree", revision)
            tree_sha = sha(
                mapping(mapping(commit.get("commit")).get("tree")).get("sha")
            )
            files, source_failures, inventory = source_files(
                github,
                repository,
                registry,
                revision,
                tree_sha,
            )
            failures.extend(source_failures)
            inventories.append(inventory)
            context = Context(
                github, repository.name, revision, files, metadata, registry.max_pages
            )
            for plugin in plugins:
                try:
                    result = plugin.collect(context)
                    observations.extend(result.observations)
                    failures.extend(result.failures)
                    coverage.extend(result.coverage)
                except Exception:
                    failures.append(Failure(plugin.name, provenance, "collector_error"))
        except SourceError as error:
            failures.append(Failure("github_source", provenance, str(error)))
    return CollectionResult(
        precedence(tuple(set(observations))),
        tuple(
            sorted(
                set(failures),
                key=lambda item: (
                    item.provenance.repository,
                    item.provenance.source,
                    item.collector,
                    item.reason,
                ),
            )
        ),
        tuple(skipped),
        coverage=tuple(sorted(set(coverage))),
        inventories=tuple(inventories),
    )
