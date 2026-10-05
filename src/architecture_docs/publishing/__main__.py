"""Manually publish one saved snapshot; scheduling and credentials are external."""

import argparse
import os
import sys
from pathlib import Path

from architecture_docs.codec import snapshot_from_json
from architecture_docs.publishing import publish
from architecture_docs.publishing.google_drive import GoogleDrive, PublicationError
from architecture_docs.renderers.artifacts import RenderConfig


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--parent", required=True)
    parser.add_argument("--namespace", default="architecture-docs")
    parser.add_argument("--title-prefix", default="")
    parser.add_argument("--no-source-links", action="store_true")
    args = parser.parse_args(arguments)
    drive = None
    try:
        snapshot = snapshot_from_json(args.snapshot.read_text(encoding="utf-8"))
        config = RenderConfig(args.title_prefix, not args.no_source_links)
        drive = GoogleDrive(os.environ.get("ARCHITECTURE_DOCS_GOOGLE_ACCESS_TOKEN", ""))
        result = publish(
            snapshot,
            drive,
            args.state,
            args.parent,
            namespace=args.namespace,
            config=config,
        )
    except PublicationError, ValueError, OSError:
        parser.exit(
            2,
            "Unable to publish deterministic documents; "
            "inspect local publication state.\n",
        )
    finally:
        if drive is not None:
            drive.close()
    sys.stdout.write(result.to_json() + "\n")
    return 0 if result.complete else 1


if __name__ == "__main__":
    raise SystemExit(main())
