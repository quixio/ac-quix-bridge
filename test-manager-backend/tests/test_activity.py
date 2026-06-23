"""Tests for ActivityLog — Quix.AI SSE events → live activity feed entries."""

from datetime import datetime

from shared.post_race_ai.activity import MAX_EVENTS, TEXT_MAX, ActivityLog


def test_tool_call_start_appends_tool_entry() -> None:
    log = ActivityLog()
    changed = log.handle(
        {
            "type": "tool_call_start",
            "toolName": "mcp__quixlake__run_query",
            "displayName": "Querying lap times",
            "toolCallId": "tc1",
        }
    )
    assert changed is True
    assert len(log.events) == 1
    evt = log.events[0]
    assert evt["kind"] == "tool"
    assert evt["tool"] == "run_query"  # mcp__<guid>__ prefix stripped for display
    assert evt["label"] == "Querying lap times"
    assert isinstance(evt["ts"], datetime)


def test_tool_call_start_label_falls_back_to_tool_name() -> None:
    log = ActivityLog()
    log.handle({"type": "tool_call_start", "toolName": "get_test", "toolCallId": "x"})
    assert log.events[0]["label"] == "get_test"


def test_tool_result_patches_matching_entry_by_tool_call_id() -> None:
    log = ActivityLog()
    log.handle(
        {"type": "tool_call_start", "toolName": "run_query", "toolCallId": "tc1"}
    )
    changed = log.handle(
        {
            "type": "tool_result",
            "toolCallId": "tc1",
            "isError": False,
            "userSummary": "142 rows",
        }
    )
    assert changed is True
    # result patched in place — still a single entry, not a new one
    assert len(log.events) == 1
    assert log.events[0]["result"] == "142 rows"
    assert log.events[0]["error"] is False


def test_tool_result_is_error_sets_error_true() -> None:
    log = ActivityLog()
    log.handle({"type": "tool_call_start", "toolName": "run_query", "toolCallId": "t"})
    log.handle({"type": "tool_result", "toolCallId": "t", "isError": True})
    assert log.events[0]["error"] is True


def test_tool_result_unknown_id_is_noop() -> None:
    log = ActivityLog()
    changed = log.handle({"type": "tool_result", "toolCallId": "ghost"})
    assert changed is False
    assert log.events == []


def test_environment_agent_start_is_bare_header() -> None:
    log = ActivityLog()
    changed = log.handle(
        {"type": "environment_agent_start", "task": "deep telemetry dive — internal"}
    )
    assert changed is True
    evt = log.events[0]
    assert evt["kind"] == "agent_start"
    assert evt["label"] == "Analysis sandbox"
    assert evt.get("result") is None  # raw task prompt not surfaced


def test_environment_agent_activity_appends_agent_step_with_sub() -> None:
    log = ActivityLog()
    log.handle(
        {
            "type": "environment_agent_activity",
            "kind": "command",
            "data": "python analyze_stints.py",
        }
    )
    evt = log.events[0]
    assert evt["kind"] == "agent_step"
    assert evt["sub"] == "command"
    assert evt["label"] == "python analyze_stints.py"


def test_agent_activity_object_data_extracts_command_label() -> None:
    # Live SSE sends `data` as a per-kind object, not a string.
    log = ActivityLog()
    log.handle(
        {
            "type": "environment_agent_activity",
            "kind": "command",
            "data": {"kind": "command", "command": "echo hi", "exitCode": 0},
        }
    )
    assert log.events[0]["label"] == "echo hi"


def test_agent_activity_file_edit_uses_path() -> None:
    log = ActivityLog()
    log.handle(
        {
            "type": "environment_agent_activity",
            "kind": "file_edit",
            "data": {"path": "/tmp/analyze.py", "linesChanged": 12},
        }
    )
    assert log.events[0]["label"] == "/tmp/analyze.py"


def test_tool_name_mcp_prefix_stripped() -> None:
    log = ActivityLog()
    log.handle(
        {
            "type": "tool_call_start",
            "toolName": "mcp__33331ffcb64a40738cd18069e781339d__run_query",
            "displayName": "Run query",
            "toolCallId": "t",
        }
    )
    assert log.events[0]["tool"] == "run_query"
    assert log.events[0]["label"] == "Run query"


def test_tool_name_without_prefix_unchanged() -> None:
    log = ActivityLog()
    log.handle(
        {"type": "tool_call_start", "toolName": "delegate_task", "toolCallId": "t"}
    )
    assert log.events[0]["tool"] == "delegate_task"


def test_multiline_label_keeps_first_nonempty_line() -> None:
    log = ActivityLog()
    log.handle(
        {
            "type": "environment_agent_activity",
            "kind": "command",
            "data": {"command": "\nDownloading pandas\nInstalled 9 packages\nDONE"},
        }
    )
    assert log.events[0]["label"] == "Downloading pandas"


def test_sandbox_tool_grouped_into_one_row() -> None:
    # tool_start + command + tool_result of the same toolUseId => ONE row.
    log = ActivityLog()
    log.handle(
        {
            "type": "environment_agent_activity",
            "kind": "tool_start",
            "data": {"displayName": "Run command", "tool": "Bash", "toolUseId": "u1"},
        }
    )
    log.handle(
        {
            "type": "environment_agent_activity",
            "kind": "command",
            "data": {"command": "echo hi", "toolUseId": "u1"},
        }
    )
    log.handle(
        {
            "type": "environment_agent_activity",
            "kind": "tool_result",
            "data": {"summary": "hi\n", "isError": False, "toolUseId": "u1"},
        }
    )
    assert len(log.events) == 1
    e = log.events[0]
    assert e["kind"] == "agent_step"
    assert e["label"] == "Run command"
    assert e["detail"] == "echo hi"
    assert e["sub"] == "command"
    assert not e.get("error")


def test_sandbox_write_file_grouped_with_path() -> None:
    log = ActivityLog()
    log.handle(
        {
            "type": "environment_agent_activity",
            "kind": "tool_start",
            "data": {"displayName": "Write file", "toolUseId": "w1"},
        }
    )
    log.handle(
        {
            "type": "environment_agent_activity",
            "kind": "file_edit",
            "data": {"path": "/tmp/analyze.py", "linesChanged": 12, "toolUseId": "w1"},
        }
    )
    e = log.events[0]
    assert e["label"] == "Write file"
    assert e["detail"] == "/tmp/analyze.py"
    assert e["sub"] == "file_edit"


def test_sandbox_tool_result_error_flips_row_red() -> None:
    log = ActivityLog()
    log.handle(
        {
            "type": "environment_agent_activity",
            "kind": "tool_start",
            "data": {"displayName": "Run command", "toolUseId": "u9"},
        }
    )
    changed = log.handle(
        {
            "type": "environment_agent_activity",
            "kind": "tool_result",
            "data": {"isError": True, "toolUseId": "u9"},
        }
    )
    assert changed is True
    assert len(log.events) == 1  # no extra row for the result
    assert log.events[0]["error"] is True


def test_sandbox_tool_result_error_without_row_surfaces_failure() -> None:
    # isError result with no matching tool_start (no toolUseId) still shows up.
    log = ActivityLog()
    changed = log.handle(
        {
            "type": "environment_agent_activity",
            "kind": "tool_result",
            "data": {"isError": True, "summary": "boom"},
        }
    )
    assert changed is True
    assert len(log.events) == 1
    assert log.events[0]["error"] is True
    assert log.events[0]["sub"] == "error"


def test_sandbox_error_kind_surfaces_string_data() -> None:
    log = ActivityLog()
    log.handle(
        {
            "type": "environment_agent_activity",
            "kind": "error",
            "data": "connection reset",
        }
    )
    e = log.events[0]
    assert e["kind"] == "agent_step"
    assert e["error"] is True
    assert e["label"] == "connection reset"


def test_sandbox_narration_and_output_dropped() -> None:
    # text/status/complete/disconnected + orphan tool_result produce no rows.
    log = ActivityLog()
    for kind, data in [
        ("text", {"text": "Here are the computed results for session ..."}),
        ("status", {"message": "reconnecting"}),
        ("complete", {"summary": "done"}),
        ("disconnected", {}),
        ("tool_result", {"summary": "URL: set", "toolUseId": "orphan"}),
    ]:
        assert (
            log.handle(
                {"type": "environment_agent_activity", "kind": kind, "data": data}
            )
            is False
        )
    assert log.events == []


def test_environment_agent_end_failed_marks_error() -> None:
    log = ActivityLog()
    log.handle(
        {
            "type": "environment_agent_end",
            "summary": "crashed",
            "status": "failed",
        }
    )
    evt = log.events[0]
    assert evt["kind"] == "agent_end"
    assert evt["label"] == "Sandbox finished"
    assert evt["error"] is True
    assert evt.get("result") is None  # summary not surfaced — it's in the report


def test_environment_agent_end_completed_no_error() -> None:
    log = ActivityLog()
    log.handle(
        {"type": "environment_agent_end", "summary": "3 stints", "status": "completed"}
    )
    assert log.events[0]["error"] is False


def test_ignored_events_return_false_and_append_nothing() -> None:
    log = ActivityLog()
    for etype in ("text_delta", "tool_call_delta", "usage", "status"):
        assert log.handle({"type": etype}) is False
    assert log.events == []


def test_cap_drops_oldest_keeping_most_recent() -> None:
    log = ActivityLog(cap=5)
    for i in range(8):
        log.handle(
            {"type": "environment_agent_activity", "kind": "command", "data": f"s{i}"}
        )
    assert len(log.events) == 5
    # FIFO: oldest (s0..s2) dropped, newest kept
    assert log.events[0]["label"] == "s3"
    assert log.events[-1]["label"] == "s7"


def test_tool_result_for_evicted_entry_is_noop() -> None:
    # cap=2: the tracked tool entry gets pushed out before its result lands.
    log = ActivityLog(cap=2)
    log.handle(
        {"type": "tool_call_start", "toolName": "run_query", "toolCallId": "tc1"}
    )
    log.handle({"type": "environment_agent_activity", "kind": "command", "data": "a"})
    log.handle({"type": "environment_agent_activity", "kind": "command", "data": "b"})
    # tc1 has been evicted — a late result must not resurrect or patch anything.
    changed = log.handle(
        {"type": "tool_result", "toolCallId": "tc1", "userSummary": "late"}
    )
    assert changed is False
    assert len(log.events) == 2
    assert all(e.get("result") != "late" for e in log.events)


def test_long_free_text_is_clipped() -> None:
    log = ActivityLog()
    log.handle(
        {
            "type": "environment_agent_activity",
            "kind": "command",
            "data": "x" * 5000,
        }
    )
    label = log.events[0]["label"]
    assert len(label) == TEXT_MAX
    assert label.endswith("…")


def test_max_events_default_is_bounded() -> None:
    assert MAX_EVENTS == 200
