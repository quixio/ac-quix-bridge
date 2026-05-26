# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx", "python-dotenv"]
# ///
"""Upload a resource file into an existing Knowledge Base.

Does NOT touch KB metadata (title/description). Adds a new resource OR
replaces a prior resource with the same filename, then triggers reprocess.

Get the KB ID from `uv run list_kbs.py` or from quix-ai-config/.env after
`create_kb.py`.

Usage:
    uv run update_kb.py --kb-id <id> path/to/file.md

Call repeatedly with different files to add multiple resources to the same KB.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sys

from _common import upload_kb_resource


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--kb-id",
        default=os.environ.get("POST_RACE_SUMMARY_KB_ID"),
        help=(
            "Target KB ID. Defaults to $POST_RACE_SUMMARY_KB_ID "
            "(set by create_kb.py in quix-ai-config/.env). "
            "Pass explicitly to target a different KB."
        ),
    )
    parser.add_argument("md_path", help="Path to the markdown file to upload")
    args = parser.parse_args(argv)

    if not args.kb_id:
        parser.error(
            "--kb-id not provided and $POST_RACE_SUMMARY_KB_ID not set. "
            "Run create_kb.py first or pass --kb-id explicitly."
        )

    md_path = pathlib.Path(args.md_path).resolve()
    if not md_path.is_file():
        print(f"File not found: {md_path}")
        return 1

    print(f"Uploading {md_path.name} to KB {args.kb_id}")
    upload_kb_resource(args.kb_id, md_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
