"""Explicit repository and content approvals; no discovery of the whole account."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from architecture_docs.estate import EstateContract
    from architecture_docs.infisical_scope import InfisicalConfig

ArchivedPolicy = Literal["exclude", "include", "only"]


@dataclass(frozen=True)
class Repository:
    name: str
    paths: tuple[str, ...]
    archived: ArchivedPolicy = "exclude"
    ref: str | None = None

    def __post_init__(self) -> None:
        if not re.fullmatch(
            r"[A-Za-z0-9_-][A-Za-z0-9_.-]*/[A-Za-z0-9_-][A-Za-z0-9_.-]*", self.name
        ):
            raise ValueError("invalid repository identity")
        if self.archived not in ("exclude", "include", "only"):
            raise ValueError("invalid archived policy")
        if not self.paths or any(
            not pattern
            or pattern.startswith("/")
            or ".." in pattern.split("/")
            or "**" in pattern
            for pattern in self.paths
        ):
            raise ValueError("invalid approved paths")
        if self.ref is not None and (
            not isinstance(self.ref, str) or not self.ref or len(self.ref) > 255
        ):
            raise ValueError("invalid source ref")

    def approves(self, path: str) -> bool:
        """Sensitive file classes remain excluded even under a broad approval."""
        parts = PurePosixPath(path).parts
        forbidden = {".git", ".ssh", ".aws", "secrets", "credentials", "node_modules"}
        return (
            bool(parts)
            and not path.startswith("/")
            and ".." not in parts
            and not forbidden.intersection(part.lower() for part in parts)
            and not any(part.lower().startswith(".env") for part in parts)
            and not path.lower().endswith((".pem", ".key", ".p12", ".tfstate"))
            and any(
                len(parts) == len(pattern.split("/"))
                and all(
                    fnmatchcase(part, selector)
                    for part, selector in zip(parts, pattern.split("/"), strict=True)
                )
                for pattern in self.paths
            )
        )


@dataclass(frozen=True)
class Registry:
    repositories: tuple[Repository, ...]
    max_file_bytes: int = 262_144
    max_files: int = 500
    max_pages: int = 10
    estate: EstateContract | None = None
    infisical: InfisicalConfig | None = None

    def __post_init__(self) -> None:
        names = [item.name.casefold() for item in self.repositories]
        if len(names) != len(set(names)):
            raise ValueError("duplicate repository identity")
        if not (0 < self.max_file_bytes <= 1_048_576):
            raise ValueError("invalid file bound")
        if not (0 < self.max_files <= 5000 and 0 < self.max_pages <= 100):
            raise ValueError("invalid collection bound")
        if self.estate is not None:
            expected = {item.name for item in self.estate.repositories}
            if set(names) - {name.casefold() for name in expected}:
                raise ValueError("approved repository missing estate expectation")
            if any(item.name not in expected for item in self.repositories):
                raise ValueError("estate repository identity casing mismatch")
        if self.infisical is not None and any(
            binding.repository not in {r.name for r in self.repositories}
            for binding in self.infisical.bindings
        ):
            raise ValueError("unapproved Infisical consumer")


def load_registry(path: Path) -> Registry:
    """Reject unknown options and invalid shapes instead of broadening scope."""
    # Declarations also use Repository validation; defer this import to avoid a cycle.
    from architecture_docs.estate import contract_from_data  # noqa: PLC0415

    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError, ValueError:
        raise ValueError("invalid registry input") from None
    if (
        set(data) - {"version", "repositories", "limits", "estate", "infisical_scope"}
        or data.get("version") != 1
    ):
        raise ValueError("unsupported registry contract")
    entries = data.get("repositories", [])
    if not isinstance(entries, list):
        raise ValueError("invalid repository list")
    repositories = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("invalid repository entry")
        if set(entry) - {"name", "paths", "archived", "ref"}:
            raise ValueError("unknown repository option")
        if (
            not isinstance(entry.get("name"), str)
            or not isinstance(entry.get("paths"), list)
            or not all(isinstance(item, str) for item in entry["paths"])
        ):
            raise ValueError("invalid repository entry")
        repositories.append(
            Repository(
                entry["name"],
                tuple(entry["paths"]),
                entry.get("archived", "exclude"),
                entry.get("ref"),
            )
        )
    limits = data.get("limits", {})
    if not isinstance(limits, dict) or set(limits) - {
        "max_file_bytes",
        "max_files",
        "max_pages",
    }:
        raise ValueError("unknown collection limit")
    if any(type(value) is not int for value in limits.values()):
        raise ValueError("invalid collection limit")
    try:
        estate = contract_from_data(data["estate"]) if "estate" in data else None
    except ValueError, TypeError, KeyError:
        raise ValueError("invalid estate contract") from None
    from architecture_docs.infisical_scope import load_infisical  # noqa: PLC0415

    scope_file = data.get("infisical_scope")
    if scope_file is not None and (
        not isinstance(scope_file, str)
        or Path(scope_file).is_absolute()
        or ".." in Path(scope_file).parts
    ):
        raise ValueError("invalid Infisical approval path")
    infisical = (
        load_infisical(path.parent / scope_file, {r.name for r in repositories})
        if scope_file
        else None
    )
    return Registry(
        tuple(sorted(repositories, key=lambda item: item.name)),
        estate=estate,
        infisical=infisical,
        **limits,
    )
