"""Project JSON before decoding: excluded strings never become Python values.

Only named metadata leaves are decoded. All other scalar tokens are scanned as
bytes. A secret-value token must be Infisical's fixed mask, empty or null; any
other token fails closed before json.loads can see it. No body/error is logged.
"""

import json
import re

from architecture_docs.collectors.github import SourceError

_TOKEN = re.compile(
    rb'"(?:[^"\\\x00-\x1f]|\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4}))*"|true|false|null|-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?'
)
_LEAVES = frozenset(
    {
        "id",
        "_id",
        "workspace",
        "projectId",
        "environment",
        "secretPath",
        "secretKey",
        "version",
        "updatedAt",
        "secretValueHidden",
        "name",
    }
)
_CONTAINERS = frozenset({"secrets", "imports", "workspace"})


def metadata_json(body: bytes | bytearray) -> object:  # noqa: PLR0915
    position = 0

    def whitespace() -> None:
        nonlocal position
        while position < len(body) and body[position] in b" \r\n\t":
            position += 1

    def token() -> bytes:
        nonlocal position
        whitespace()
        match = _TOKEN.match(body, position)
        if match is None:
            raise SourceError("metadata_decode_error")
        position = match.end()
        return bytes(match[0])

    def consume(mark: int) -> None:
        nonlocal position
        whitespace()
        if position >= len(body) or body[position] != mark:
            raise SourceError("metadata_decode_error")
        position += 1

    def value(keep: bool, depth: int = 0) -> object:  # noqa: PLR0912, PLR0915
        nonlocal position
        whitespace()
        if depth > 20 or position >= len(body):
            raise SourceError("metadata_decode_error")
        if body[position] == ord("{"):
            position += 1
            result: dict[str, object] = {}
            seen = set()
            whitespace()
            while position < len(body) and body[position] != ord("}"):
                raw_key = token()
                if not raw_key.startswith(b'"'):
                    raise SourceError("metadata_decode_error")
                key = json.loads(raw_key)
                if key in seen:
                    raise SourceError("duplicate_metadata_field")
                seen.add(key)
                consume(ord(":"))
                if key.lower() in {
                    "secretvalue",
                    "value",
                    "secretvalueciphertext",
                    "encryptedvalue",
                    "password",
                    "credential",
                    "token",
                }:
                    raw = token()
                    if raw not in {b'"<hidden-by-infisical>"', b'""', b"null"}:
                        raise SourceError("unexpected_value_field")
                else:
                    selected = keep and key in _LEAVES | _CONTAINERS
                    item = value(selected, depth + 1)
                    if selected:
                        result[key] = item
                whitespace()
                if position < len(body) and body[position] == ord(","):
                    position += 1
                    whitespace()
                    if position < len(body) and body[position] == ord("}"):
                        raise SourceError("metadata_decode_error")
                else:
                    break
            consume(ord("}"))
            return result if keep else None
        if body[position] == ord("["):
            position += 1
            entries = []
            whitespace()
            while position < len(body) and body[position] != ord("]"):
                item = value(keep, depth + 1)
                if keep:
                    entries.append(item)
                whitespace()
                if position < len(body) and body[position] == ord(","):
                    position += 1
                    whitespace()
                    if position < len(body) and body[position] == ord("]"):
                        raise SourceError("metadata_decode_error")
                else:
                    break
            consume(ord("]"))
            return entries if keep else None
        raw = token()
        return json.loads(raw) if keep else None

    try:
        result = value(True)
        whitespace()
        if position != len(body):
            raise SourceError("metadata_decode_error")
        return result
    except ValueError, UnicodeError:
        raise SourceError("metadata_decode_error") from None
