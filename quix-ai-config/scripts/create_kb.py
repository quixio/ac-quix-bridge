# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx", "python-dotenv"]
# ///
"""Create a new (empty) Knowledge Base in Quix.AI.

KB metadata only — no resources uploaded. Add resources afterwards with
`update_kb.py --kb-id <id> <path-to-md>`.

Usage:
    uv run create_kb.py --title "Post Race Summary" [--description "..."]

Writes <TITLE_SLUG>_KB_ID=<id> to quix-ai-config/.env on success
(e.g. "Post Race Summary" -> POST_RACE_SUMMARY_KB_ID).
"""

from __future__ import annotations

import argparse
import re
import sys

from _common import http_client, write_env


def _env_key_from_title(title: str) -> str:
    """'Post Race Summary' -> 'POST_RACE_SUMMARY_KB_ID'."""
    upper = title.upper()
    slug = re.sub(r"[^A-Z0-9]+", "_", upper).strip("_")
    return f"{slug}_KB_ID"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--title", required=True, help="Display title of the KB")
    parser.add_argument(
        "--description",
        default="",
        help="Optional description shown in the Quix.AI UI",
    )
    args = parser.parse_args(argv)

    body = {"title": args.title, "description": args.description}

    with http_client() as client:
        print(f"Creating KB (title={args.title!r})")
        r = client.post("/api/org/knowledge-bases", json=body)
        r.raise_for_status()
        kb_id = r.json()["id"]
        print(f"  id={kb_id}")

    write_env(_env_key_from_title(args.title), kb_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
