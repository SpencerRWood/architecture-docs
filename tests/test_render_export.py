"""Local export validates snapshots, emits bounded metadata, and stays deterministic."""

import json
from pathlib import Path

import pytest

from architecture_docs.renderers.__main__ import main
from architecture_docs.renderers.artifacts import DocumentKind
from test_rendering import representative


def test_local_export_writes_stable_markdown_files_and_structured_artifacts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(representative().to_json())
    output = tmp_path / "documents"
    assert main([str(snapshot), str(output)]) == 0
    metadata = json.loads(capsys.readouterr().out)
    assert metadata["document_count"] == 15
    assert metadata["publication_blocked"] is False
    assert {path.stem for path in output.glob("*.md")} == {
        kind.value for kind in DocumentKind
    }
    contents = {path.name: path.read_text() for path in output.iterdir()}
    artifacts = json.loads(contents["document-set.json"])
    assert artifacts["snapshot_id"] == metadata["snapshot_id"]
    assert main([str(snapshot), str(output)]) == 0
    capsys.readouterr()
    assert contents == {path.name: path.read_text() for path in output.iterdir()}
    assert "fixture-sensitive-literal" not in "".join(contents.values())


def test_configured_export_can_disable_links_and_add_safe_title_prefix(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(representative().to_json())
    output = tmp_path / "documents"
    assert (
        main(
            [
                str(snapshot),
                str(output),
                "--title-prefix",
                "Team / ",
                "--no-source-links",
            ]
        )
        == 0
    )
    capsys.readouterr()
    text = (output / "architecture-overview.md").read_text()
    assert text.startswith("# Team / Architecture Overview")
    assert "https://github.com/" not in text


def test_invalid_snapshot_or_io_failure_has_no_raw_error_body(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text("fixture-sensitive-literal")
    output = tmp_path / "documents"
    with pytest.raises(SystemExit) as error:
        main([str(snapshot), str(output)])
    assert error.value.code == 2
    assert not output.exists()
    captured = capsys.readouterr()
    assert "fixture-sensitive-literal" not in captured.err
    assert captured.err == "Unable to render or export deterministic documents.\n"
    snapshot.write_text(representative().to_json())
    output.write_text("existing-file")
    with pytest.raises(SystemExit):
        main([str(snapshot), str(output)])
    assert (
        capsys.readouterr().err
        == "Unable to render or export deterministic documents.\n"
    )
