"""Bounded native Drive/Docs transport; responses and credentials stay in memory."""

import json
import re
from typing import Any

import httpx

FOLDER = "application/vnd.google-apps.folder"
DOCUMENT = "application/vnd.google-apps.document"
FIELDS = "id,mimeType,parents,trashed,appProperties"


class PublicationError(Exception):
    """A fixed reason code, never server text, source content or credentials."""


def identity(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", value):
        raise PublicationError("invalid_identity")
    return value


def mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PublicationError("malformed_response")
    return value


class GoogleDrive:
    """Explicit OAuth access token with drive.file scope and approved parent access."""

    def __init__(
        self, token: str, *, transport: httpx.BaseTransport | None = None
    ) -> None:
        if not token or any(ord(char) < 32 for char in token):
            raise PublicationError("missing_or_invalid_token")
        self._client = httpx.Client(
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
            follow_redirects=False,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def request(
        self,
        method: str,
        path: str,
        *,
        docs: bool = False,
        params: dict[str, str] | None = None,
        body: dict[str, object] | None = None,
    ) -> dict[str, Any]:
        base = (
            "https://docs.googleapis.com/v1/documents"
            if docs
            else "https://www.googleapis.com/drive/v3/files"
        )
        try:
            with self._client.stream(
                method, base + path, params=params, json=body
            ) as response:
                if response.status_code != 200:
                    reason = {
                        400: "invalid_request_or_revision",
                        401: "unauthorized",
                        403: "access_denied",
                        404: "missing_file",
                        429: "rate_limit",
                    }.get(response.status_code, "http_error")
                    raise PublicationError(reason)
                payload = bytearray()
                for chunk in response.iter_bytes():
                    payload.extend(chunk)
                    if len(payload) > 8_388_608:
                        raise PublicationError("response_too_large")
                return mapping(json.loads(payload))
        except httpx.HTTPError, ValueError:
            raise PublicationError("transport_or_decode_error") from None

    def file(self, file_id: str) -> dict[str, Any]:
        return self.request("GET", f"/{identity(file_id)}", params={"fields": FIELDS})

    def find(self, parent: str, scope: str, key: str) -> dict[str, Any] | None:
        # All selectors are fixed literals or validated identities; no query injection.
        query = (
            f"'{identity(parent)}' in parents and trashed = false and "
            f"appProperties has {{ key='architecture_scope' and "
            f"value='{identity(scope)}' }} and "
            f"appProperties has {{ key='architecture_artifact' and "
            f"value='{identity(key)}' }}"
        )
        result = self.request(
            "GET",
            "",
            params={
                "q": query,
                "pageSize": "100",
                "fields": f"nextPageToken,files({FIELDS})",
            },
        )
        files = result.get("files")
        if not isinstance(files, list):
            raise PublicationError("malformed_response")
        if result.get("nextPageToken") or len(files) > 1:
            raise PublicationError("ambiguous_identity")
        return mapping(files[0]) if files else None

    def create(
        self, parent: str, scope: str, key: str, title: str, mime: str
    ) -> dict[str, Any]:
        return self.request(
            "POST",
            "",
            params={"fields": FIELDS},
            body={
                "name": title,
                "mimeType": mime,
                "parents": [identity(parent)],
                "appProperties": {
                    "architecture_scope": identity(scope),
                    "architecture_artifact": identity(key),
                },
            },
        )

    def document(self, file_id: str) -> dict[str, Any]:
        return self.request("GET", f"/{identity(file_id)}", docs=True)

    def replace(self, file_id: str, revision: str, end_index: int, text: str) -> None:
        requests: list[dict[str, object]] = []
        if end_index > 2:
            requests.append(
                {
                    "deleteContentRange": {
                        "range": {"startIndex": 1, "endIndex": end_index - 1}
                    }
                }
            )
        requests.append({"insertText": {"location": {"index": 1}, "text": text}})
        self.request(
            "POST",
            f"/{identity(file_id)}:batchUpdate",
            docs=True,
            body={
                "writeControl": {"requiredRevisionId": revision},
                "requests": requests,
            },
        )


def document_text(document: dict[str, Any]) -> tuple[str, str, int]:
    """Accept only the publisher's simple single-tab body; protect other topology."""
    revision = document.get("revisionId")
    content = mapping(document.get("body")).get("content")
    if not isinstance(revision, str) or not revision or not isinstance(content, list):
        raise PublicationError("unsupported_document_structure")
    if document.get("tabs") or document.get("headers") or document.get("footers"):
        raise PublicationError("unsupported_document_structure")
    text = ""
    end = 2
    for entry in content:
        item = mapping(entry)
        if "sectionBreak" in item:
            continue
        paragraph = mapping(item.get("paragraph"))
        if paragraph.get("bullet"):
            raise PublicationError("unsupported_document_structure")
        for part in paragraph.get("elements", []):
            element = mapping(part)
            if any(key.startswith("suggested") for key in element):
                raise PublicationError("unsupported_document_structure")
            run = mapping(element.get("textRun"))
            value = run.get("content")
            if not isinstance(value, str):
                raise PublicationError("unsupported_document_structure")
            text += value
        item_end = item.get("endIndex")
        if type(item_end) is not int:
            raise PublicationError("unsupported_document_structure")
        end = item_end
    if not text.endswith("\n") or end != 1 + len(text.encode("utf-16-le")) // 2:
        raise PublicationError("unsupported_document_structure")
    # Google Docs retains one final newline which cannot be deleted.
    return text[:-1], revision, end
