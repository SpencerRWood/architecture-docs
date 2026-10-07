"""Explicit exact-path approvals and consumer associations; no credentials."""

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from architecture_docs.declarations import canonical, identifier


@dataclass(frozen=True, order=True)
class Scope:
    project: str
    environment: str
    path: str

    def __post_init__(self) -> None:
        for value in (self.project, self.environment, self.path):
            identifier(value)
            if "*" in value or ":" in value:
                raise ValueError("invalid Infisical scope")
        if not re.fullmatch(r"/(?:[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*)?", self.path):
            raise ValueError("invalid Infisical path")
        if any(
            not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value)
            for value in (self.project, self.environment)
        ):
            raise ValueError("invalid Infisical project/environment")

    @property
    def source(self) -> str:
        return f"infisical:{self.project}/{self.environment}{self.path}"


@dataclass(frozen=True, order=True)
class Binding:
    repository: str
    project: str
    environment: str
    path: str
    consumer: str = ""
    key: str = ""
    injection: str = ""
    required: str = ""
    owner: str = ""

    def __post_init__(self) -> None:
        Scope(self.project, self.environment, self.path)
        from architecture_docs.config import Repository  # noqa: PLC0415

        Repository(self.repository, ("*",))
        for value in (
            self.repository,
            self.consumer,
            self.key,
            self.injection,
            self.owner,
        ):
            if value:
                identifier(value)
        if bool(self.consumer) != bool(self.key):
            raise ValueError("alias requires consumer and key")
        if self.required not in {"", "true", "false"}:
            raise ValueError("invalid required state")


@dataclass(frozen=True)
class InfisicalConfig:
    url: str
    scopes: tuple[Scope, ...]
    bindings: tuple[Binding, ...] = ()
    max_secrets: int = 500
    max_response_bytes: int = 1_048_576

    def __post_init__(self) -> None:
        url = urlsplit(self.url)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            or url.path not in {"", "/"}
        ):
            raise ValueError("invalid Infisical URL")
        if (
            not self.scopes
            or len(self.scopes) > 100
            or len(set(self.scopes)) != len(self.scopes)
        ):
            raise ValueError("invalid approved Infisical scopes")
        if not (
            type(self.max_secrets) is int
            and type(self.max_response_bytes) is int
            and 0 < self.max_secrets <= 5000
            and 0 < self.max_response_bytes <= 8_388_608
        ):
            raise ValueError("invalid Infisical bound")
        if any(
            Scope(b.project, b.environment, b.path) not in self.scopes
            for b in self.bindings
        ):
            raise ValueError("binding outside approved scope")
        if len(set(self.bindings)) != len(self.bindings):
            raise ValueError("duplicate Infisical binding")

    @property
    def contract(self) -> str:
        return canonical(asdict(self))


def config_from_data(data: dict[str, Any]) -> InfisicalConfig:
    scopes = data.pop("scopes")
    bindings = data.pop("bindings")
    if not isinstance(scopes, list) or not isinstance(bindings, list):
        raise ValueError("invalid Infisical metadata shape")
    return InfisicalConfig(
        scopes=tuple(Scope(**item) for item in scopes),
        bindings=tuple(Binding(**item) for item in bindings),
        **data,
    )


def validate_metadata_observation(key: str, value: str) -> None:
    from architecture_docs.collectors.infisical import Location  # noqa: PLC0415

    try:
        data = json.loads(value)
        if key == "infisical.location":
            Location(**data)
        elif key == "infisical.contract":
            config_from_data(data)
        else:
            raise ValueError("unknown Infisical observation")
    except ValueError, TypeError, KeyError:
        raise ValueError("invalid Infisical metadata observation") from None


def load_infisical(path: Path, repositories: set[str]) -> InfisicalConfig:
    import tomllib  # noqa: PLC0415

    try:
        data = tomllib.loads(path.read_text())
        if data.pop("version") != 1:
            raise ValueError("unsupported Infisical contract")
        config = InfisicalConfig(
            scopes=tuple(sorted(Scope(**item) for item in data.pop("scopes"))),
            bindings=tuple(
                sorted(Binding(**item) for item in data.pop("bindings", []))
            ),
            **data,
        )
        if any(b.repository not in repositories for b in config.bindings):
            raise ValueError("unapproved Infisical consumer")
        return config
    except OSError, KeyError, TypeError, ValueError:
        raise ValueError("invalid Infisical approval contract") from None
