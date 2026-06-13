# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx", "python-dotenv"]
# ///
"""Upload resource file(s) into a Knowledge Base, then trigger processing once.

Each file is added, or replaces a prior resource with the same filename. After
the uploads, one KB-level /process is fired — processing is incremental
server-side (only new/changed resources re-distill; unchanged are skipped), so
a single trigger over the whole KB is enough.

Resolve the KB id from --kb-id, or --env-key (looked up in the selected .env,
where create_kb.py stored it).

Usage:
    uv run upload_kb_resource.py --env-key AC_TELEMETRY_KB_ID a.md b.md   # upload + process
    uv run upload_kb_resource.py --kb-id <id> a.md --wait                 # + poll to completion
    uv run upload_kb_resource.py --kb-id <id> a.md --no-process           # upload only
    uv run upload_kb_resource.py --kb-id <id>                             # process only (no files)
"""

from __future__ import annotations

import argparse
import pathlib
import sys

from _common import process_kb, read_env_value, upload_kb_resource


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="*", help="Markdown resource files to upload")
    parser.add_argument("--kb-id", default=None, help="Target KB id (overrides --env-key)")
    parser.add_argument(
        "--env-key",
        default=None,
        help="`.env` var holding the KB id (e.g. AC_TELEMETRY_KB_ID), as written by create_kb.py",
    )
    parser.add_argument(
        "--no-process",
        action="store_true",
        help="Upload only; don't trigger processing",
    )
    parser.add_argument(
        "--wait",
        action="store_true",
        help="Block and poll until processing completes (default: fire and return)",
    )
    args = parser.parse_args(argv)

    kb_id = args.kb_id or (read_env_value(args.env_key) if args.env_key else None)
    if not kb_id:
        parser.error("provide --kb-id, or --env-key naming a KB id stored in the .env")

    paths: list[pathlib.Path] = []
    for f in args.files:
        p = pathlib.Path(f).resolve()
        if not p.is_file():
            print(f"File not found: {p}")
            return 1
        paths.append(p)

    for p in paths:
        print(f"Uploading {p.name} to KB {kb_id}")
        upload_kb_resource(kb_id, p)

    if args.no_process:
        if paths:
            print("Skipped processing (--no-process); trigger it later.")
        return 0

    print(f"Processing KB {kb_id}")
    process_kb(kb_id, wait=args.wait)
    return 0


if __name__ == "__main__":
    sys.exit(main())
