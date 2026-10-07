"""Bounded GET-only Infisical metadata, with fixed server-side value masking."""

import os
import re
from dataclasses import asdict, dataclass
from hashlib import sha256

import httpx

from architecture_docs.collectors.github import SourceError, mapping, sequence, text
from architecture_docs.collectors.metadata_boundary import metadata_json
from architecture_docs.declarations import canonical, identifier
from architecture_docs.infisical_scope import InfisicalConfig, Scope
from architecture_docs.model import (
    Authority,
    CollectionResult,
    Failure,
    Observation,
    Provenance,
    SourceCoverage,
)

COLLECTOR = "infisical-metadata-v1"
NAMESPACE = "infisical/metadata"


@dataclass(frozen=True, order=True)
class Location:
    project: str
    project_name: str
    environment: str
    path: str
    key: str
    object_id: str
    version: int

    def __post_init__(self) -> None:
        Scope(self.project, self.environment, self.path)
        identifier(self.object_id)
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,199}", self.key):
            raise ValueError("invalid secret key")
        if not re.fullmatch(r"[A-Za-z0-9 _.-]{1,100}", self.project_name):
            raise ValueError("invalid project name")
        if type(self.version) is not int or self.version < 1:
            raise ValueError("invalid metadata object version")


class Infisical:
    def __init__(
        self,
        config: InfisicalConfig,
        token: str | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.config = config
        self._token = token
        self._client = httpx.Client(
            base_url=config.url,
            headers={"Authorization": f"Bearer {token or ''}"},
            timeout=30,
            follow_redirects=False,
            transport=transport,
        )

    @classmethod
    def from_environment(cls, config: InfisicalConfig) -> Infisical:
        return cls(config, os.environ.get("ARCHITECTURE_DOCS_INFISICAL_METADATA_TOKEN"))

    def close(self) -> None:
        self._client.close()

    def _get(self, endpoint: str, params: dict[str, str]) -> object:
        if not self._token:
            raise SourceError("metadata_credentials_missing")
        try:
            with self._client.stream("GET", endpoint, params=params) as response:
                if response.status_code != 200:
                    raise SourceError(
                        {
                            401: "unauthorized",
                            403: "access_denied",
                            404: "not_found",
                            429: "rate_limit",
                        }.get(response.status_code, "http_error")
                    )
                body = bytearray()
                try:
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body) > self.config.max_response_bytes:
                            raise SourceError("metadata_response_limit")
                    return metadata_json(body)
                finally:
                    body.clear()
        except httpx.HTTPError:
            raise SourceError("metadata_transport_error") from None

    def locations(self, scope: Scope) -> tuple[Location, ...]:
        if scope not in self.config.scopes:
            raise SourceError("unapproved_metadata_scope")
        project = mapping(
            mapping(self._get(f"/api/v1/workspace/{scope.project}", {})).get(
                "workspace"
            )
        )
        if project.get("id", project.get("_id")) != scope.project:
            raise SourceError("metadata_scope_mismatch")
        name = text(project.get("name"))
        payload = mapping(
            self._get(
                "/api/v4/secrets",
                {
                    "projectId": scope.project,
                    "environment": scope.environment,
                    "secretPath": scope.path,
                    "viewSecretValue": "false",
                    "expandSecretReferences": "false",
                    "recursive": "false",
                    "includeImports": "false",
                    "includePersonalOverrides": "false",
                },
            )
        )
        if payload.get("imports", []) != []:
            raise SourceError("unexpected_secret_imports")
        entries = sequence(payload.get("secrets"))
        if len(entries) > self.config.max_secrets:
            raise SourceError("metadata_secret_limit")
        result = []
        try:
            for entry in entries:
                item = mapping(entry)
                if (
                    item.get("workspace") != scope.project
                    or item.get("environment") != scope.environment
                    or item.get("secretPath") != scope.path
                    or item.get("secretValueHidden") is not True
                ):
                    raise SourceError("metadata_scope_or_mask_mismatch")
                result.append(
                    Location(
                        scope.project,
                        name,
                        scope.environment,
                        scope.path,
                        text(item.get("secretKey")),
                        text(item.get("id", item.get("_id"))),
                        item["version"],
                    )
                )
        except ValueError, TypeError, KeyError:
            raise SourceError("invalid_metadata_identifier") from None
        if len({item.object_id for item in result}) != len(result):
            raise SourceError("duplicate_metadata_object")
        return tuple(sorted(result))

    def collect(self) -> CollectionResult:
        observations = [
            Observation(
                COLLECTOR,
                Authority.CONFIGURATION,
                "infisical.contract",
                self.config.contract,
                Provenance(NAMESPACE, "infisical:approval", None),
            )
        ]
        failures = []
        coverage = [SourceCoverage(NAMESPACE, "infisical:approval", COLLECTOR, None)]
        for scope in sorted(self.config.scopes):
            provenance = Provenance(NAMESPACE, scope.source, None)
            try:
                locations = self.locations(scope)
                # Object version is safe mutable metadata, NOT a Git commit.
                # Canonical metadata digest provides a repeatable inventory revision.
                revision = (
                    "metadata-sha256:"
                    + sha256(
                        canonical([asdict(item) for item in locations]).encode()
                    ).hexdigest()
                )
                observations.extend(
                    Observation(
                        COLLECTOR,
                        Authority.CONFIGURATION,
                        "infisical.location",
                        canonical(asdict(item)),
                        Provenance(
                            NAMESPACE,
                            f"{scope.source}/{item.key}/{item.object_id}",
                            revision,
                        ),
                    )
                    for item in locations
                )
                coverage.append(
                    SourceCoverage(NAMESPACE, scope.source, COLLECTOR, revision)
                )
            except SourceError as error:
                failures.append(Failure(COLLECTOR, provenance, str(error)))
        return CollectionResult(
            tuple(observations), tuple(failures), coverage=tuple(coverage)
        )
