"""System prompt + output-schema contract for the plot agent.

The agent receives (on the FIRST turn of a Quix AI chat session only):
  - Instructions on how to respond
  - Condensed channel list
  - Current lake sessions list
  - The user's natural-language prompt

Every subsequent turn, only the raw user prompt is sent — Quix AI's server-side
session memory carries the context forward.

Output contract: the agent's reply MUST contain exactly one JSON block fenced
with ```json ... ``` at the end of its response. Everything before the fence
is ignored by the parser (so the agent can "think out loud" if it wants).

The JSON is a discriminated union on `type`:

    {"type": "plot",    "title": "...", "signal": "speedKmh", "traces": [...]}
    {"type": "clarify", "question": "...", "options": ["..."]}

Trace shape: {session_id, lap, driver, carModel, track, experiment, environment, test_rig}
— the partition-column subset plus lap, so the backend can call the telemetry
fetcher without further inference. `signal` is a single column name (we overlay
laps of one signal per chart; multi-signal is deferred to v2).
"""

from __future__ import annotations

import csv
import io

from .channels import channels_for_prompt

INSTRUCTIONS = """You are a telemetry query agent. The user describes a plot; you reply with either a concrete plot request or a clarifying question. Your reply MUST end with one fenced ```json``` block. Text before it is optional prose.

Output shapes (pick one):

```json
{"type": "plot", "title": "<short human title>", "signals": ["<col1>", "<col2>"], "traces": [
  {"session_id": "...", "lap": 1, "driver": "...", "carModel": "...", "track": "...", "experiment": "...", "environment": "...", "test_rig": "..."}
]}
```

```json
{"type": "clarify", "question": "<one sentence>", "options": ["<chip 1>", "<chip 2>"]}
```

Rules:
- `signals` is an array of 1-10 column names (left-hand side of `name = label` lines from the channel list). Each becomes a separate chart; the same trace list applies to all. Default `["speedKmh"]`. Don't add signals the user didn't ask for — more charts is noisier, not better.
- Every trace's partition values MUST come from the sessions table. Never invent IDs.
- "all laps" expands the `laps` list for each matching row → one trace per lap.
- Cap traces at 6. Over 6 → respond with `clarify` asking the user to narrow by driver, date, or experiment.
- Traces MUST share one track (overlaying different tracks on normalizedCarPosition is meaningless). If the match spans tracks, `clarify`.
- Clarify `options` are strings; each becomes a clickable chip sent back verbatim as the user's next message.
- Sessions table columns: environment, test_rig, experiment, driver, track, carModel, session_id, laps. `laps` is pipe-separated (e.g. `1|2|3`).
"""


SESSION_COLUMNS = (
    "environment",
    "test_rig",
    "experiment",
    "driver",
    "track",
    "carModel",
    "session_id",
    "laps",
)


def sessions_as_csv(sessions: list[dict[str, object]]) -> str:
    """Render the sessions list as CSV. `laps` (a list[int]) collapses to
    pipe-separated (e.g. `1|2|3`) so commas stay the column delimiter.

    Uses `csv.writer` so any partition value containing a comma or quote is
    properly escaped rather than silently corrupting row alignment.

    CSV over JSON saves ~2 KB for 10 sessions because the 8 column keys are
    written once in the header rather than repeated per row.
    """
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(SESSION_COLUMNS)
    for s in sessions:
        row: list[str] = []
        for col in SESSION_COLUMNS:
            val = s.get(col, "")
            if col == "laps" and isinstance(val, list):
                row.append("|".join(str(v) for v in val))
            else:
                row.append(str(val))
        writer.writerow(row)
    return buf.getvalue().rstrip("\n")


def build_first_turn_message(
    *,
    user_message: str,
    sessions: list[dict[str, object]],
) -> str:
    """Compose the payload sent as the user message on turn 1 of a new chat.

    We wrap instructions + channels + sessions + the user's request in one
    message because the Quix AI POST shape is `{message, context}` — no
    separate system slot. The agent memorizes everything in this message for
    the life of the session; subsequent turns can send just the raw prompt.

    Portal caps messages at 10,000 bytes; keep an eye on the combined size
    of channels + sessions if either grows.
    """
    return f"""{INSTRUCTIONS}

# Channels (ac_telemetry columns)

```
{channels_for_prompt()}
```

# Sessions (CSV)

```
{sessions_as_csv(sessions)}
```

# User request

{user_message}
"""
