"""Decorator that wraps every MCP tool callable with structured logging.

DEBUG entry: tool name + sorted kwarg keys (never values).
DEBUG exit: duration + ok.
WARN on raise with exception class + duration.
"""

import functools
import inspect
import logging
import time
from typing import Any, Callable


def instrument_tool(
    name: str, fn: Callable[..., Any], logger: logging.Logger
) -> Callable[..., Any]:
    """Wrap an MCP tool so each dispatch emits structured log entries."""
    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            logger.debug("mcp tool: %s called (kwargs=%s)", name, sorted(kwargs.keys()))
            start = time.perf_counter()
            try:
                result = await fn(*args, **kwargs)
            except Exception as exc:
                dur_ms = (time.perf_counter() - start) * 1000
                logger.warning(
                    "mcp tool: %s raised %s after %.1fms — %s",
                    name,
                    type(exc).__name__,
                    dur_ms,
                    exc,
                )
                raise
            dur_ms = (time.perf_counter() - start) * 1000
            logger.debug("mcp tool: %s ok in %.1fms", name, dur_ms)
            return result

        return async_wrapper

    @functools.wraps(fn)
    def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
        logger.debug("mcp tool: %s called (kwargs=%s)", name, sorted(kwargs.keys()))
        start = time.perf_counter()
        try:
            result = fn(*args, **kwargs)
        except Exception as exc:
            dur_ms = (time.perf_counter() - start) * 1000
            logger.warning(
                "mcp tool: %s raised %s after %.1fms — %s",
                name,
                type(exc).__name__,
                dur_ms,
                exc,
            )
            raise
        dur_ms = (time.perf_counter() - start) * 1000
        logger.debug("mcp tool: %s ok in %.1fms", name, dur_ms)
        return result

    return sync_wrapper
