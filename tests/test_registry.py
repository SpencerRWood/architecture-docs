"""Registry approvals and invalid input fail closed."""

from pathlib import Path

import pytest

from architecture_docs.config import Registry, Repository, load_registry
from architecture_docs.model import Authority, Observation, Provenance, precedence


def test_repository_registry() -> None:
    loaded = load_registry(Path("config/repositories.toml"))
    assert loaded.repositories[0].archived == "exclude"
    assert loaded.repositories[0].approves(".github/workflows/release.yml")
    assert not loaded.repositories[0].approves("private.txt")


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        ".env.example",
        "nested/.env.prod",
        "secrets/file.yml",
        "credentials/token.json",
        "key.pem",
        "key.key",
        "key.p12",
        "state.tfstate",
        "../README.md",
        "/README.md",
        ".git/config",
        "node_modules/package.json",
        "",
    ],
)
def test_sensitive_paths_are_never_approved(path: str) -> None:
    assert not Repository("owner/repo", ("*",)).approves(path)


@pytest.mark.parametrize("name", ["invalid", "https://github.com/o/r", "../repo"])
def test_invalid_identity(name: str) -> None:
    with pytest.raises(ValueError, match="identity"):
        Repository(name, ("*",))


@pytest.mark.parametrize("paths", [(), ("",), ("/absolute",), ("../file",)])
def test_invalid_approval(paths: tuple[str, ...]) -> None:
    with pytest.raises(ValueError, match="paths"):
        Repository("o/r", paths)


def test_invalid_limits_and_duplicate_repositories() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        Registry((Repository("o/r", ("*",)), Repository("O/R", ("*",))))
    with pytest.raises(ValueError, match="file bound"):
        Registry((), max_file_bytes=0)
    with pytest.raises(ValueError, match="collection bound"):
        Registry((), max_pages=0)
    with pytest.raises(ValueError, match="ref"):
        Repository("o/r", ("*",), ref="")


@pytest.mark.parametrize(
    "content",
    [
        "version=2",
        "version=1\nunknown=true",
        "version=1\n[limits]\nunknown=2",
        "version=1\n[limits]\nmax_files=true",
        "version=1\n[limits]\nmax_pages='ten'",
        "version=1\n[repositories]\nname='o/r'",
        "version=1\nrepositories=[1]",
        'version=1\n[[repositories]]\nname="o/r"\npaths=["*"]\nunknown=true',
        'version=1\n[[repositories]]\nname="o/r"\npaths="*"',
        'version=1\n[[repositories]]\nname="o/r"\npaths=[2]',
        'version=1\n[[repositories]]\nname="o/r"\npaths=["*"]\narchived="bad"',
    ],
)
def test_registry_rejects_invalid_contract(tmp_path: Path, content: str) -> None:
    path = tmp_path / "registry.toml"
    path.write_text(content)
    with pytest.raises(ValueError, match=r"unsupported|unknown|invalid"):
        load_registry(path)


def test_registry_parse_errors_are_normalized(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="invalid registry input"):
        load_registry(tmp_path / "missing")
    path = tmp_path / "registry.toml"
    path.write_text("[malformed")
    with pytest.raises(ValueError, match="invalid registry input"):
        load_registry(path)


def test_source_precedence_preserves_conflicting_evidence() -> None:
    source = Provenance("o/r", "source", "a" * 40)
    observations = tuple(
        Observation("fixture", authority, "same.fact", authority.name, source)
        for authority in reversed(Authority)
    )
    ordered = precedence(observations)
    assert len(ordered) == 4
    assert ordered[0].authority == Authority.CONFIGURATION
    assert ordered[-1].authority == Authority.GITHUB
