# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx", "python-dotenv"]
# ///
"""Create (or adopt) an empty Knowledge Base in Quix.AI.

KB metadata only — no resources uploaded. Add resources afterwards with
`upload_kb_resource.py --kb-id <id> <path-to-md>`.

Idempotent (ID-first): reuses the id already stored under the env-key if it
still exists on the server, else adopts an existing KB with the same title,
else creates a new one.

Usage:
    uv run create_kb.py --title "AC Telemetry" --env-key AC_TELEMETRY_KB_ID [--description "..."]

Writes the resolved id to the selected .env under --env-key (defaults to a
slug of the title, e.g. "Post Race Summary" -> POST_RACE_SUMMARY_KB_ID).
"""

from __future__ import annotations

import argparse
import re
import sys

from _common import http_client, read_env_value, write_env


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
    parser.add_argument(
        "--env-key",
        default=None,
        help=(
            "`.env` var to store the resolved KB id under (e.g. AC_TELEMETRY_KB_ID). "
            "Defaults to a slug of the title."
        ),
    )
    args = parser.parse_args(argv)

    env_key = args.env_key or _env_key_from_title(args.title)

    with http_client() as client:
        existing = client.get("/api/org/knowledge-bases").json()

        # 1. ID-first: reuse the id already stored under env_key if it still exists.
        stored_id = read_env_value(env_key)
        if stored_id and any(kb.get("id") == stored_id for kb in existing):
            print(f"KB already exists (id={stored_id}); reusing for {env_key}.")
            write_env(env_key, stored_id)
            return 0

        # 2. Title bootstrap: adopt an existing KB with this title.
        matches = [kb for kb in existing if kb.get("title") == args.title]
        if len(matches) > 1:
            print(
                f"WARNING: {len(matches)} KBs titled {args.title!r}; adopting the first. "
                "Use a unique title or delete duplicates."
            )
        if matches:
            kb_id = matches[0]["id"]
            print(f"Adopting existing KB titled {args.title!r} (id={kb_id}).")
            write_env(env_key, kb_id)
            return 0

        # 3. Create a new KB.
        print(f"Creating KB (title={args.title!r})")
        r = client.post(
            "/api/org/knowledge-bases",
            json={"title": args.title, "description": args.description},
        )
        r.raise_for_status()
        kb_id = r.json()["id"]
        print(f"  id={kb_id}")

    write_env(env_key, kb_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
