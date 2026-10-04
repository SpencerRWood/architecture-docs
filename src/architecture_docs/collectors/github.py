"""Bounded GET-only GitHub access. Never log source bodies or server errors."""

import base64
import hashlib
import json
import re
from typing import Any
from urllib.parse import quote

import httpx


class SourceError(Exception):
    """Only a normalized, non-sensitive failure reason crosses the boundary."""


def mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise SourceError("malformed_response")
    return value


def sequence(value: object) -> list[Any]:
    if not isinstance(value, list):
        raise SourceError("malformed_response")
    return value


def text(value: object) -> str:
    if not isinstance(value, str):
        raise SourceError("malformed_response")
    return value


def sha(value: object) -> str:
    result = text(value)
    if not re.fullmatch(r"[0-9a-f]{40}", result):
        raise SourceError("malformed_revision")
    return result


class GitHub:
    """Use a fine-grained token with Contents/Metadata/Actions read only."""

    def __init__(
        self,
        token: str | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.Client(
            base_url="https://api.github.com",
            headers=headers,
            timeout=30,
            follow_redirects=False,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def get(self, path: str) -> object:
        if not path.startswith("/repos/") or ".." in path or ":" in path:
            raise SourceError("invalid_endpoint")
        try:
            with self._client.stream("GET", path) as response:
                if response.status_code != 200:
                    reason = {
                        403: "access_or_rate_limit",
                        404: "not_found",
                        429: "rate_limit",
                        401: "unauthorized",
                    }.get(response.status_code, "http_error")
                    raise SourceError(reason)
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > 8_388_608:
                        raise SourceError("response_too_large")
                return json.loads(body)
        except httpx.HTTPError, ValueError:
            raise SourceError("transport_or_decode_error") from None

    def pages(self, path: str, max_pages: int, key: str | None = None) -> list[Any]:
        """Do not follow untrusted Link URLs; mark bounded overflow incomplete."""
        values: list[Any] = []
        for page in range(1, max_pages + 1):
            result = self.get(f"{path}?per_page=100&page={page}")
            entries = sequence(result if key is None else mapping(result).get(key))
            values.extend(entries)
            if len(entries) < 100:
                return values
        raise SourceError("pagination_limit")

    def blob(self, repository: str, blob_sha: str, max_bytes: int) -> str:
        data = mapping(self.get(f"/repos/{repository}/git/blobs/{blob_sha}"))
        if data.get("encoding") != "base64" or data.get("sha") != blob_sha:
            raise SourceError("malformed_blob")
        try:
            content = base64.b64decode(
                "".join(text(data.get("content")).split()), validate=True
            )
            if len(content) > max_bytes:
                raise SourceError("file_too_large")
            digest = hashlib.sha1(
                f"blob {len(content)}\0".encode() + content,
                usedforsecurity=False,
            ).hexdigest()
            if digest != blob_sha:
                raise SourceError("blob_integrity_error")
            return content.decode("utf-8")
        except ValueError, UnicodeError:
            raise SourceError("malformed_blob") from None


def encoded_ref(ref: str) -> str:
    return quote(ref, safe="")
