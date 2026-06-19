"""Turn Quix.AI SSE events into a compact live activity feed.

The runner already intercepts the agent's SSE stream; `ActivityLog` distils the
subset worth showing a human — tool calls and DEEP-mode (`delegate_task`)
sandbox steps — into an ordered list that gets persisted on the Analysis doc and
polled by the frontend. Events the user can't act on (text/tool deltas, usage,
phase status) are ignored. `handle` returns whether the list changed so the
runner can flush only when there's something new.
"""

from datetime import datetime, timezone
from typing import Any

MAX_EVENTS = 200  # bound the doc — DEEP-mode sandbox steps are chatty
TEXT_MAX = 500  # clip any free-text label/detail; sandbox `data` can be huge


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _clip(value: Any) -> str | None:
    """First line of a free-text field, length-capped (None passes through).

    Sandbox `data` and agent summaries are multi-line walls of command/query
    output; a progress feed wants the headline, not the transcript (the full
    text lives in summary_md). So we keep only the first non-empty line.
    """
    if value is None:
        return None
    text = value if isinstance(value, str) else str(value)
    first = next((ln.strip() for ln in text.splitlines() if ln.strip()), text.strip())
    return first if len(first) <= TEXT_MAX else first[: TEXT_MAX - 1] + "…"


def _short_tool(name: str | None) -> str | None:
    """Strip the `mcp__<serverGuid>__` prefix so the tool name reads cleanly."""
    if name and name.startswith("mcp__"):
        parts = name.split("__", 2)
        if len(parts) == 3:
            return parts[2]
    return name


class ActivityLog:
    """Accumulate activity-feed entries from raw Quix.AI events.

    Each entry is a plain dict matching the `ActivityEvent` model
    (`{ts, kind, tool?, label, detail?, result?, error?, sub?}`), ready to
    `$set` on the Analysis doc. Top-level `tool_result` events patch their
    `tool` entry by `toolCallId`; sandbox tool_start/command/file_edit/
    tool_result of one `toolUseId` collapse into a single `agent_step` row.
    """

    def __init__(self, cap: int = MAX_EVENTS) -> None:
        self._cap = cap
        self.events: list[dict[str, Any]] = []
        # toolCallId -> the entry dict (held by reference; patched by tool_result)
        self._by_tool_call: dict[str, dict[str, Any]] = {}
        # sandbox toolUseId -> entry; groups tool_start + command/file_edit +
        # tool_result of one DEEP-mode tool into a single row
        self._by_use_id: dict[str, dict[str, Any]] = {}

    def dump(self) -> list[dict[str, Any]]:
        return self.events

    def handle(self, evt: dict[str, Any]) -> bool:
        """Fold one SSE event into the feed. Returns True if the feed changed."""
        etype = evt.get("type")

        if etype == "tool_call_start":
            tool = _short_tool(evt.get("toolName"))
            self._append(
                {
                    "ts": _now(),
                    "kind": "tool",
                    "tool": tool,
                    "label": _clip(evt.get("displayName")) or tool or "tool",
                },
                tool_call_id=evt.get("toolCallId"),
            )
            return True

        if etype == "tool_result":
            tcid = evt.get("toolCallId")
            entry = self._by_tool_call.get(tcid) if tcid else None
            if entry is None:
                return False  # no matching start (or none registered / evicted)
            entry["result"] = _clip(evt.get("userSummary"))
            entry["error"] = bool(evt.get("isError"))
            return True

        if etype == "environment_agent_start":
            # Just a header to anchor the nested steps — the raw delegate_task
            # prompt (evt.task) is internal plumbing, not shown.
            self._append(
                {"ts": _now(), "kind": "agent_start", "label": "Analysis sandbox"}
            )
            return True

        if etype == "environment_agent_activity":
            return self._handle_agent_activity(evt)

        if etype == "environment_agent_end":
            self._append(
                {
                    "ts": _now(),
                    "kind": "agent_end",
                    "label": "Sandbox finished",
                    "error": evt.get("status") not in (None, "completed"),
                }
            )
            return True

        return False

    def _handle_agent_activity(self, evt: dict[str, Any]) -> bool:
        """One DEEP-mode sandbox activity.

        Group the tool_start + command/file_edit + tool_result of the same
        `toolUseId` into ONE row: the tool's displayName plus its command/path.
        Tool output and the agent's narration text are dropped — a progress feed
        wants "what's it doing", not the transcript (that's in summary_md).
        """
        kind = evt.get("kind")
        data = evt.get("data")
        d = data if isinstance(data, dict) else {}
        use_id = d.get("toolUseId")

        if kind == "tool_start":
            self._append(
                {
                    "ts": _now(),
                    "kind": "agent_step",
                    "label": _clip(d.get("displayName"))
                    or _clip(d.get("tool"))
                    or "step",
                },
                use_id=use_id,
            )
            return True

        if kind in ("command", "file_edit"):
            raw = (
                (d.get("command") if kind == "command" else d.get("path"))
                if d
                else data
            )
            detail = _clip(raw)
            entry = self._by_use_id.get(use_id) if use_id else None
            if entry is not None:
                entry["detail"] = detail  # attach to the tool_start row
                entry["sub"] = kind
                return True
            self._append(
                {
                    "ts": _now(),
                    "kind": "agent_step",
                    "sub": kind,
                    "label": detail or kind,
                }
            )
            return True

        if kind == "tool_result":
            entry = self._by_use_id.get(use_id) if use_id else None
            if entry is not None:
                if d.get("isError"):
                    entry["error"] = True  # flip the row red; don't surface output
                    return True
                return False
            if d.get("isError"):
                # No tool_start row to patch (missing toolUseId / evicted) — still
                # surface the failure rather than swallowing it.
                self._append(
                    {
                        "ts": _now(),
                        "kind": "agent_step",
                        "sub": "error",
                        "label": "Tool failed",
                        "error": True,
                    }
                )
                return True
            return False

        if kind == "error":
            self._append(
                {
                    "ts": _now(),
                    "kind": "agent_step",
                    "sub": "error",
                    "label": _clip(d.get("message") if d else data) or "error",
                    "error": True,
                }
            )
            return True

        # text / status / complete / disconnected → narration or dupes; drop.
        return False

    def _append(
        self,
        entry: dict[str, Any],
        *,
        tool_call_id: str | None = None,
        use_id: str | None = None,
    ) -> None:
        self.events.append(entry)
        if tool_call_id:
            self._by_tool_call[tool_call_id] = entry
        if use_id:
            self._by_use_id[use_id] = entry
        if len(self.events) > self._cap:
            dropped = self.events.pop(0)
            # Drop dangling correlation refs to the evicted entry so a late
            # result can't patch a dict no longer in the feed.
            if self._by_tool_call:
                self._by_tool_call = {
                    k: v for k, v in self._by_tool_call.items() if v is not dropped
                }
            if self._by_use_id:
                self._by_use_id = {
                    k: v for k, v in self._by_use_id.items() if v is not dropped
                }
