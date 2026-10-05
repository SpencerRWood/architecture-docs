"""Representative offline GitHub fixtures prove boundaries, not live access."""

import base64
import hashlib
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

from architecture_docs.collection import collect
from architecture_docs.collectors.content import executable, parsed
from architecture_docs.collectors.contracts import Context, SourceFile
from architecture_docs.collectors.github import GitHub, SourceError
from architecture_docs.config import Registry, Repository
from architecture_docs.model import Authority, CollectionResult, Observation, Provenance

FIXTURES = Path(__file__).parent / "fixtures" / "repository"
REVISION = "a" * 40
TREE = "b" * 40
REPOSITORY = "fixture/service"


class FixtureGitHub:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.archived = False
        self.truncated = False
        self.fail_path: str | None = None
        self.status = 403
        self.overrides: dict[str, Any] = {}
        self.files: dict[str, bytes] = {
            path.relative_to(FIXTURES).as_posix(): path.read_bytes()
            for path in FIXTURES.rglob("*")
            if path.is_file()
        }
        self.files[".env"] = b"REAL_SECRET=fixture-sensitive-literal"
        self.files["private/credentials.key"] = b"fixture-sensitive-literal"

    @staticmethod
    def digest(body: bytes) -> str:
        return hashlib.sha1(
            f"blob {len(body)}\0".encode() + body, usedforsecurity=False
        ).hexdigest()

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path.removeprefix(f"/repos/{REPOSITORY}")
        if self.fail_path is not None and self.fail_path in request.url.path:
            return httpx.Response(
                self.status, json={"message": "fixture-sensitive-literal"}
            )
        if path in self.overrides:
            return httpx.Response(200, json=self.overrides[path])
        if request.url.path == "/repos/fixture/broken":
            return httpx.Response(404)
        if not path:
            data: Any = {"default_branch": "main", "archived": self.archived}
        elif path.startswith("/commits/"):
            data = {"sha": REVISION, "commit": {"tree": {"sha": TREE}}}
        elif path.startswith("/git/trees/"):
            data = {
                "truncated": self.truncated,
                "tree": [
                    {
                        "path": name,
                        "type": "blob",
                        "mode": "100644",
                        "size": len(body),
                        "sha": self.digest(body),
                    }
                    for name, body in reversed(list(self.files.items()))
                ],
            }
        elif path.startswith("/git/blobs/"):
            digest = path.rsplit("/", 1)[1]
            body = next(
                body for body in self.files.values() if self.digest(body) == digest
            )
            data = {
                "sha": digest,
                "encoding": "base64",
                "content": base64.b64encode(body).decode(),
            }
        elif path == "/actions/workflows":
            data = {"workflows": [{"id": 10, "path": ".github/workflows/release.yml"}]}
        elif path == "/releases":
            data = [
                {"id": 20, "tag_name": "v1.2.3", "body": "fixture-sensitive-literal"}
            ]
        else:
            raise AssertionError(f"unexpected GET {request.url.path}")
        return httpx.Response(200, json=data)

    def client(self) -> GitHub:
        return GitHub("fixture-token", transport=httpx.MockTransport(self.handle))


def registry(**limits: int) -> Registry:
    return Registry((Repository(REPOSITORY, ("*",)),), **limits)


def test_read_only_deterministic_provenance_and_sensitive_exclusion() -> None:
    fixture = FixtureGitHub()
    github = fixture.client()
    result = collect(registry(), github)
    repeated = collect(registry(), github)
    github.close()
    assert result.complete
    assert result.to_json() == repeated.to_json()
    assert all(request.method == "GET" for request in fixture.requests)
    assert "fixture-sensitive-literal" not in result.to_json()
    assert "fixture-token" not in result.to_json()
    assert "postgresql://sensitive" not in result.to_json()
    forbidden = {
        fixture.digest(fixture.files[".env"]),
        fixture.digest(fixture.files["private/credentials.key"]),
    }
    assert not any(
        request.url.path.rsplit("/", 1)[1] in forbidden for request in fixture.requests
    )
    assert {item.authority for item in result.observations} == set(Authority)
    assert [item.authority for item in result.observations] == sorted(
        item.authority for item in result.observations
    )
    content = [
        item for item in result.observations if item.authority != Authority.GITHUB
    ]
    assert all(
        item.provenance.revision == REVISION
        and item.provenance.blob
        and item.provenance.repository == REPOSITORY
        for item in content
    )
    facts = {(item.key, item.value) for item in result.observations}
    assert ("package.dependency", "dagster") in facts
    assert ("service.api.depends_on", "database") in facts
    assert ("service.api.environment_name", "API_TOKEN") in facts
    assert ("workflow.secret_name", "RELEASE_TOKEN") in facts
    assert ("terraform.declaration", "resource:postgresql_database:analytics") in facts
    assert ("contract.kind", ".sh") in facts
    assert ("shell.executable", "docker") in facts
    assert ("python.declaration", "deploy") in facts
    assert ("ansible.role", "dagster") in facts
    assert ("ansible.module", "ansible.builtin.service") in facts
    assert ("ansible.variable_name", "API_SECRET") in facts
    assert ("package.dependencies", "react") in facts
    assert ("container.base_image", "python:3.14-slim") in facts
    assert ("documentation.repository_reference", "SpencerRWood/workflows") in facts
    assert ("github.tag_name", "v1.2.3") in facts
    assert any(TREE in request.url.path for request in fixture.requests)


@pytest.mark.parametrize(
    ("archived", "policy", "skipped"),
    [
        (True, "exclude", True),
        (True, "include", False),
        (True, "only", False),
        (False, "only", True),
        (False, "include", False),
        (False, "exclude", False),
    ],
)
def test_archive_policy(archived: bool, policy: Any, skipped: bool) -> None:
    fixture = FixtureGitHub()
    fixture.archived = archived
    result = collect(
        Registry((Repository(REPOSITORY, ("*",), policy),)), fixture.client()
    )
    assert bool(result.skipped) == skipped
    if skipped:
        assert len(fixture.requests) == 1
        assert not result.observations


def test_one_repository_failure_does_not_corrupt_peers() -> None:
    fixture = FixtureGitHub()
    result = collect(
        Registry(
            (Repository("fixture/broken", ("*",)), Repository(REPOSITORY, ("*",)))
        ),
        fixture.client(),
    )
    assert not result.complete
    assert result.failures[0].reason == "not_found"
    assert result.observations
    assert all(item.provenance.repository == REPOSITORY for item in result.observations)


def test_file_failure_preserves_other_observations_and_hides_errors() -> None:
    fixture = FixtureGitHub()
    fixture.fail_path = fixture.digest(fixture.files["pyproject.toml"])
    result = collect(registry(), fixture.client())
    assert not result.complete
    assert result.failures[0].reason == "access_or_rate_limit"
    assert result.failures[0].provenance.source == "pyproject.toml"
    assert result.observations
    assert "fixture-sensitive-literal" not in result.to_json()


def test_malformed_configuration_is_isolated() -> None:
    fixture = FixtureGitHub()
    fixture.files["pyproject.toml"] = b"[not valid"
    result = collect(registry(), fixture.client())
    assert any(item.reason == "parse_error" for item in result.failures)
    assert any(item.key == "workflow.uses" for item in result.observations)


def test_actions_on_key_uses_yaml12_without_changing_global_safe_loader() -> None:
    source = SourceFile(
        Provenance(
            "fixture/service", ".github/workflows/validate.yml", "a" * 40, "b" * 40
        ),
        "on:\n  push:\n    branches: [main]\nenabled: true\n"
        "jobs:\n  validation:\n    uses: "
        "SpencerRWood/workflows/.github/workflows/validate.yml@v1\n",
    )
    data = parsed(source)
    assert "on" in data
    assert data["enabled"] is True
    assert ("workflow.job", "validation") in executable(source)
    assert True in yaml.safe_load("on: push")


def test_invalid_json_cannot_be_accepted_as_yaml() -> None:
    fixture = FixtureGitHub()
    fixture.files["package.json"] = b"name: valid-yaml-but-invalid-json"
    result = collect(registry(), fixture.client())
    assert any(
        item.provenance.source == "package.json" and item.reason == "parse_error"
        for item in result.failures
    )


@pytest.mark.parametrize(
    ("kind", "reason"),
    [
        ("tree", "incomplete_tree"),
        ("files", "file_count_limit"),
        ("bytes", "file_too_large"),
    ],
)
def test_bounds_explicitly_report_incomplete(kind: str, reason: str) -> None:
    fixture = FixtureGitHub()
    fixture.truncated = kind == "tree"
    limits = (
        {"max_files": 1}
        if kind == "files"
        else ({"max_file_bytes": 1} if kind == "bytes" else {})
    )
    result = collect(registry(**limits), fixture.client())
    assert not result.complete
    assert reason in {item.reason for item in result.failures}


class ExtraCollector:
    name = "extra"

    def collect(self, context: Context) -> CollectionResult:
        return CollectionResult(
            (
                Observation(
                    self.name,
                    Authority.EXECUTABLE,
                    "contract",
                    "fixture",
                    Provenance(context.repository, "fixture", context.revision),
                ),
            )
        )


class BrokenCollector:
    name = "broken"

    def collect(self, context: Context) -> CollectionResult:
        raise RuntimeError(context.repository + "fixture-sensitive-literal")


def test_plugins_need_no_orchestration_branches() -> None:
    result = collect(
        registry(), FixtureGitHub().client(), (BrokenCollector(), ExtraCollector())
    )
    assert result.failures[0].reason == "collector_error"
    assert result.observations[0].collector == "extra"
    assert "fixture-sensitive-literal" not in result.to_json()
    with pytest.raises(ValueError, match="duplicate collector"):
        collect(
            registry(), FixtureGitHub().client(), (ExtraCollector(), ExtraCollector())
        )


@pytest.mark.parametrize("status", [401, 403, 404, 429, 500, 302])
def test_http_failures_and_redirects_are_not_followed(status: int) -> None:
    fixture = FixtureGitHub()
    fixture.fail_path = "/repos/"
    fixture.status = status
    result = collect(registry(), fixture.client())
    assert not result.complete
    assert len(fixture.requests) == 1
    assert "fixture-sensitive-literal" not in result.to_json()


def test_metadata_failure_does_not_erase_content() -> None:
    fixture = FixtureGitHub()
    fixture.fail_path = "/releases"
    result = collect(registry(), fixture.client())
    assert any(item.reason == "access_or_rate_limit" for item in result.failures)
    assert any(item.key == "package.name" for item in result.observations)


def test_invalid_tree_entry_and_file_mode_are_structured() -> None:
    fixture = FixtureGitHub()
    fixture.overrides[f"/git/trees/{TREE}"] = {
        "truncated": False,
        "tree": [
            {"path": "README.md", "type": "blob", "mode": "120000", "sha": "c" * 40},
            {"path": "compose.yml", "type": "blob", "mode": "100644", "sha": "bad"},
            {
                "path": "main.tf",
                "type": "blob",
                "mode": "100644",
                "sha": "d" * 40,
                "size": -1,
            },
        ],
    }
    result = collect(registry(), fixture.client())
    assert {item.reason for item in result.failures} == {
        "unsupported_file_mode",
        "malformed_revision",
        "malformed_response",
    }


def test_blob_integrity_and_decoding_errors() -> None:
    fixture = FixtureGitHub()
    digest = fixture.digest(fixture.files["README.md"])
    path = f"/git/blobs/{digest}"
    for blob, expected in [
        ({"encoding": "raw", "sha": digest}, "malformed_blob"),
        ({"encoding": "base64", "sha": digest, "content": "!!!"}, "malformed_blob"),
        (
            {"encoding": "base64", "sha": digest, "content": "YWJj"},
            "blob_integrity_error",
        ),
    ]:
        fixture.overrides[path] = blob
        with pytest.raises(SourceError, match=expected):
            fixture.client().blob(REPOSITORY, digest, 1000)
    del fixture.overrides[path]
    with pytest.raises(SourceError, match="file_too_large"):
        fixture.client().blob(REPOSITORY, digest, 1)


def test_pagination_has_a_bound_and_explicit_failure() -> None:
    fixture = FixtureGitHub()
    fixture.overrides["/releases"] = [
        {"id": index, "tag_name": "v1"} for index in range(100)
    ]
    result = collect(registry(max_pages=1), fixture.client())
    assert any(item.reason == "pagination_limit" for item in result.failures)


def test_malformed_metadata_and_transport_errors() -> None:
    fixture = FixtureGitHub()
    fixture.overrides[""] = {"archived": "unknown"}
    assert not collect(registry(), fixture.client()).complete
    fixture.overrides[""] = []
    assert not collect(registry(), fixture.client()).complete
    fixture.overrides[""] = {"archived": False, "default_branch": 123}
    assert not collect(registry(), fixture.client()).complete
    fixture.overrides["/releases"] = [{}]
    fixture.overrides[""] = {"archived": False, "default_branch": "main"}
    assert not collect(registry(), fixture.client()).complete

    def unavailable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("fixture-sensitive-literal", request=request)

    github = GitHub(transport=httpx.MockTransport(unavailable))
    result = collect(registry(), github)
    assert result.failures[0].reason == "transport_or_decode_error"
    with pytest.raises(SourceError, match="invalid_endpoint"):
        github.get("https://untrusted.example")
    assert "fixture-sensitive-literal" not in json.dumps(result.to_json())
