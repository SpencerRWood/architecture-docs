"""Export a saved normalized snapshot as local artifacts, without publication."""

import argparse
import sys
from pathlib import Path

from architecture_docs.codec import snapshot_from_json
from architecture_docs.declarations import canonical
from architecture_docs.renderers import render_documents
from architecture_docs.renderers.artifacts import RenderConfig


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--title-prefix", default="")
    parser.add_argument("--no-source-links", action="store_true")
    args = parser.parse_args(arguments)
    try:
        config = RenderConfig(args.title_prefix, not args.no_source_links)
        documents = render_documents(
            snapshot_from_json(args.snapshot.read_text(encoding="utf-8")), config
        )
        # Validate/render everything before creating any output files.
        outputs = {
            f"{doc.id}.md": doc.to_markdown(config) for doc in documents.documents
        }
        outputs["document-set.json"] = documents.to_json() + "\n"
        args.output.mkdir(parents=True, exist_ok=True)
        for name, body in outputs.items():
            (args.output / name).write_text(body, encoding="utf-8")
    except ValueError, OSError:
        parser.exit(2, "Unable to render or export deterministic documents.\n")
    sys.stdout.write(
        canonical(
            {
                "snapshot_id": documents.snapshot_id,
                "document_count": len(documents.documents),
                "output_directory": str(args.output.resolve()),
                "publication_blocked": any(
                    doc.publication_blocked for doc in documents.documents
                ),
            }
        )
        + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
