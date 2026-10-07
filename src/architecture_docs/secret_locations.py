"""Repository/service location declarations contain names and scope, never values."""

import re
from dataclasses import asdict, dataclass

from architecture_docs.declarations import identifier


@dataclass(frozen=True, order=True)
class SecretLocation:
    project: str
    environment: str
    path: str
    service: str = ""

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _-]{0,99}", self.project):
            raise ValueError("invalid secret project name")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", self.environment):
            raise ValueError("invalid secret environment")
        if not re.fullmatch(r"/(?:[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*)?", self.path):
            raise ValueError("invalid secret path")
        if self.service:
            identifier(self.service)

    def to_data(self) -> dict[str, str]:
        return asdict(self)
