"""Tool registration constants. Tools themselves live in handlers/*.py."""

# Human-readable titles surfaced via MCP `Tool.title` (spec 2025-03-26).
TOOL_TITLES: dict[str, str] = {
    "get_test": "Get test",
    "get_session": "Get session",
    "list_logbook": "List logbook entries",
    "get_driver": "Get driver",
    "get_device": "Get device",
    "get_environment": "Get environment",
    "list_sessions_for_test": "List sessions for test",
    "list_recent_sessions_for_driver": "List recent sessions for driver",
    "save_analysis": "Save analysis",
}
