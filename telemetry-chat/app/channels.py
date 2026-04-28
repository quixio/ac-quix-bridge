"""Load channels.json and emit a flat `name = label` block for the LLM prompt.

The LLM only needs the column name (for SQL) and the label (to match the
user's natural-language request). Units and categories are for the plot
axis, not the selection — the frontend looks those up from raw_channels()
when rendering.

A 5 KB flat list costs ~0.25¢ more per first-turn message than a folded
form, but removes a whole class of prompt-bug (unit bracket leakage, suffix
expansion failures) and keeps this module readable.
"""

from __future__ import annotations

import json
from functools import cache

from . import config


@cache
def raw_channels() -> dict[str, dict[str, str]]:
    with open(config.CHANNELS_FILE) as f:
        data = json.load(f)
    return {k: v for k, v in data.items() if not k.startswith("_")}


def channels_for_prompt() -> str:
    """One `name = label` line per channel. Stable, deterministic order."""
    return "\n".join(
        f"{name} = {meta.get('label', name)}" for name, meta in raw_channels().items()
    )
