"""Supplement repository contents with bounded GitHub object identity."""

from architecture_docs.collectors.content import identifiers
from architecture_docs.collectors.contracts import Context
from architecture_docs.collectors.github import SourceError, mapping
from architecture_docs.model import (
    Authority,
    CollectionResult,
    Failure,
    Observation,
    Provenance,
)


class MetadataCollector:
    name = "github_metadata"

    def collect(self, context: Context) -> CollectionResult:
        observations = [
            Observation(
                self.name,
                Authority.GITHUB,
                "repository.identity",
                context.repository,
                Provenance(context.repository, "github:repository", None),
            ),
            Observation(
                self.name,
                Authority.GITHUB,
                "repository.archived",
                str(context.metadata["archived"]).lower(),
                Provenance(context.repository, "github:repository", None),
            ),
        ]
        failures = []
        for endpoint, key, field in (
            ("actions/workflows", "workflows", "path"),
            ("releases", None, "tag_name"),
        ):
            provenance = Provenance(context.repository, f"github:{endpoint}", None)
            try:
                entries = context.github.pages(
                    f"/repos/{context.repository}/{endpoint}",
                    context.max_pages,
                    key,
                )
                for entry in entries:
                    data = mapping(entry)
                    object_id = data.get("id")
                    if not isinstance(object_id, int):
                        raise SourceError("malformed_response")
                    for value in identifiers([data.get(field)]):
                        observations.append(
                            Observation(
                                self.name,
                                Authority.GITHUB,
                                f"github.{field}",
                                value,
                                Provenance(
                                    context.repository,
                                    f"github:{endpoint}/{object_id}",
                                    value if field == "tag_name" else None,
                                ),
                            )
                        )
            except SourceError as error:
                failures.append(Failure(self.name, provenance, str(error)))
        return CollectionResult(tuple(observations), tuple(failures))
