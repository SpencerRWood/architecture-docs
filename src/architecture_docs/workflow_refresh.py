"""Re-extract approved workflow blobs at their retained immutable revisions."""

from dataclasses import replace

from architecture_docs.collectors.content import content_collectors
from architecture_docs.collectors.contracts import Context, SourceFile
from architecture_docs.collectors.github import GitHub, SourceError
from architecture_docs.config import Registry
from architecture_docs.model import CollectionResult, Failure


def refresh_workflows(
    collection: CollectionResult, registry: Registry, github: GitHub
) -> CollectionResult:
    approved = {repo.name: repo for repo in registry.repositories}
    provenances = sorted(
        {
            o.provenance
            for o in collection.observations
            if o.collector == "executable"
            and o.provenance.source.startswith(".github/workflows/")
        }
    )
    plugin = next(c for c in content_collectors() if c.name == "executable")
    observations = list(collection.observations)
    failures = list(collection.failures)
    coverage = list(collection.coverage)
    for provenance in provenances:
        if (
            provenance.repository not in approved
            or not approved[provenance.repository].approves(provenance.source)
            or not provenance.blob
            or not provenance.revision
        ):
            raise ValueError("unapproved or unpinned workflow refresh")
        try:
            source = SourceFile(
                provenance,
                github.blob(
                    provenance.repository, provenance.blob, registry.max_file_bytes
                ),
            )
            result = plugin.collect(
                Context(
                    github,
                    provenance.repository,
                    provenance.revision,
                    (source,),
                    {},
                    registry.max_pages,
                )
            )
        except SourceError as error:
            failures.append(Failure(plugin.name, provenance, str(error)))
            continue
        failures.extend(result.failures)
        if not result.complete:
            continue
        observations = [
            o
            for o in observations
            if not (o.collector == plugin.name and o.provenance == provenance)
        ]
        observations.extend(result.observations)
        coverage = [
            c
            for c in coverage
            if not (
                c.collector == plugin.name
                and (c.repository, c.source)
                == (provenance.repository, provenance.source)
            )
        ]
        coverage.extend(result.coverage)
    return replace(
        collection,
        observations=tuple(observations),
        failures=tuple(failures),
        coverage=tuple(coverage),
    )
