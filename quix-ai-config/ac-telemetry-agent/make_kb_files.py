# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx", "python-dotenv"]
# ///
"""Regenerate the AC Telemetry Agent's knowledge-base markdown files.

Outputs into the sibling `kb/` directory:

  kb_ac_channels.md   — every ac_telemetry column grouped by category
  kb_ac_sessions.md   — every partition combination currently in the lake

Reads channel metadata from `telemetry-comparison/channels.json` and
walks QuixLake's /partitions endpoint for the session list. Re-run when
new sessions appear in the lake; upload via:

    uv run ../scripts/upload_kb_resource.py --env-key AC_TELEMETRY_KB_ID kb/kb_ac_channels.md

Credentials come from `quix-ai-config/.env`. `QUIX_TOKEN` doubles as the
QuixLake bearer. `QUIXLAKE_URL` defaults to the dev cluster but can be
overridden in the same .env.

Usage:
    cd quix-ai-config/ac-telemetry-agent
    uv run make_kb_files.py
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
QUIX_AI_CONFIG = HERE.parent
REPO_ROOT = QUIX_AI_CONFIG.parent

load_dotenv(QUIX_AI_CONFIG / ".env")

QUIXLAKE_URL = os.environ.get(
    "QUIXLAKE_URL",
    "https://quixlake-quixdev-quixlakev2-dev.deployments-dev.quix.io",
).rstrip("/")
QUIX_TOKEN = os.environ["QUIX_TOKEN"]
TABLE_NAME = os.environ.get("TABLE_NAME", "ac_telemetry")
CHANNELS_FILE = REPO_ROOT / "telemetry-comparison" / "channels.json"
KB_DIR = HERE / "kb"

PARTITION_COLS = [
    "environment",
    "test_rig",
    "experiment",
    "driver",
    "track",
    "carModel",
    "session_id",
]


# ───────────────────────── channels ─────────────────────────


def build_channels_md() -> str:
    with open(CHANNELS_FILE) as f:
        data = json.load(f)
    channels = {k: v for k, v in data.items() if not k.startswith("_")}

    # Group by category, preserve insertion order inside each group.
    groups: dict[str, list[tuple[str, dict]]] = {}
    for name, meta in channels.items():
        cat = meta.get("cat", "Other")
        groups.setdefault(cat, []).append((name, meta))

    out: list[str] = []
    out.append("# AC Telemetry Channels\n")
    out.append(
        f"Columns available in the `{TABLE_NAME}` table in QuixLake, grouped "
        "by category.\n"
    )
    out.append("Column naming conventions:\n")
    out.append(
        "- Per-wheel columns use suffixes `FL`, `FR`, `RL`, `RR` "
        "(front-left, front-right, rear-left, rear-right).\n"
        "- Per-axis columns use suffixes `_x`, `_y`, `_z` "
        "(world-frame unless noted).\n"
        "- `normalizedCarPosition` ranges 0 → 1 over one lap.\n"
    )
    out.append("## Channels by category\n")

    for cat in groups:
        out.append(f"### {cat}\n")
        out.append("| Column | Label | Unit |")
        out.append("|---|---|---|")
        for name, meta in groups[cat]:
            unit = meta.get("unit", "[-]")
            label = meta.get("label", name)
            out.append(f"| `{name}` | {label} | {unit} |")
        out.append("")  # blank line between sections

    return "\n".join(out) + "\n"


# ───────────────────────── sessions ─────────────────────────


async def fetch_partitions(client: httpx.AsyncClient, path: str) -> list[str]:
    params = {"table": TABLE_NAME}
    if path:
        params["path"] = path
    r = await client.get(
        f"{QUIXLAKE_URL}/partitions",
        params=params,
        headers={"Authorization": f"Bearer {QUIX_TOKEN}"},
    )
    r.raise_for_status()
    return [p["name"] for p in r.json().get("partitions", [])]


async def walk(
    client: httpx.AsyncClient, path: str = "", depth: int = 0
) -> list[dict]:
    if depth == len(PARTITION_COLS):
        session: dict = {}
        for part in path.split("/"):
            if "=" in part:
                k, v = part.split("=", 1)
                session[k] = v
        lap_names = await fetch_partitions(client, path)
        laps: list[int] = []
        for name in lap_names:
            if name.startswith("lap="):
                try:
                    laps.append(int(name[len("lap=") :]))
                except ValueError:
                    continue
        session["laps"] = sorted(laps)
        return [session]

    children = await fetch_partitions(client, path)
    if not children:
        return []
    next_paths = [f"{path}/{c}" if path else c for c in children]
    subtrees = await asyncio.gather(*(walk(client, p, depth + 1) for p in next_paths))
    return [s for sub in subtrees for s in sub]


def build_sessions_md(sessions: list[dict]) -> str:
    """One session per H3 section so RAG chunkers keep rows intact.

    Tables get split mid-row by common chunkers, destroying column alignment
    and causing the agent to hallucinate row values. Section-per-session
    gives every row its own retrievable chunk with self-contained context.
    """
    timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    out: list[str] = []
    out.append("# AC Telemetry Sessions Index\n")
    out.append(
        "Every partition combination currently available in the "
        f"`{TABLE_NAME}` QuixLake table. One section per session; fields "
        "inside each section are self-contained so semantic retrieval can "
        "surface a single session without losing column alignment.\n"
    )
    out.append(f"_Generated: {timestamp}. Sessions: {len(sessions)}._\n")
    out.append("## Sessions\n")
    out.append(
        "Each session is described in a short prose paragraph. The values "
        "shown are the literal partition values — use them verbatim in SQL "
        "`WHERE` clauses or plot trace JSON.\n"
    )
    for s in sessions:
        laps = ", ".join(str(x) for x in s.get("laps", [])) or "(none)"
        sid = s.get("session_id") or "NA"
        driver = s.get("driver") or "NA"
        experiment = s.get("experiment") or "NA"
        track = s.get("track") or "NA"
        car = s.get("carModel") or "NA"
        env = s.get("environment") or "NA"
        rig = s.get("test_rig") or "NA"
        out.append(f"### Session {sid}\n")
        out.append(
            f"Driver `{driver}` ran experiment `{experiment}` on track "
            f"`{track}` in car `{car}` (environment `{env}`, test_rig "
            f"`{rig}`). Recorded laps: {laps}. To reference this session, "
            f"use `session_id = '{sid}'` together with `driver = '{driver}'`, "
            f"`experiment = '{experiment}'`, `track = '{track}'`, "
            f"`carModel = '{car}'`, `environment = '{env}'`, "
            f"`test_rig = '{rig}'`."
        )
        out.append("")  # blank line between sessions
    return "\n".join(out).rstrip() + "\n"


# ───────────────────────── main ─────────────────────────


async def main() -> None:
    KB_DIR.mkdir(exist_ok=True)
    channels_md = build_channels_md()
    out = KB_DIR / "kb_ac_channels.md"
    out.write_text(channels_md)
    print(f"wrote {out.relative_to(REPO_ROOT)} ({len(channels_md):,} bytes)")

    async with httpx.AsyncClient(
        timeout=30.0,
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
    ) as client:
        sessions = await walk(client)
    sessions_md = build_sessions_md(sessions)
    out = KB_DIR / "kb_ac_sessions.md"
    out.write_text(sessions_md)
    print(
        f"wrote {out.relative_to(REPO_ROOT)} ({len(sessions_md):,} bytes, {len(sessions)} sessions)"
    )


if __name__ == "__main__":
    asyncio.run(main())
