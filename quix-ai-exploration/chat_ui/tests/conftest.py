"""pytest configuration: slow/integration tests are skipped by default.

Run everything:   uv run pytest --run-slow
Only fast tests:  uv run pytest        (default)
Only slow:        uv run pytest -m slow --run-slow
Integration only: uv run pytest -m integration --run-slow
"""

from __future__ import annotations

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-slow",
        action="store_true",
        default=False,
        help="Run tests marked `slow` / `integration` (network, containers, real Portal API).",
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    if config.getoption("--run-slow"):
        return
    skip = pytest.mark.skip(reason="needs --run-slow flag")
    for item in items:
        if "slow" in item.keywords or "integration" in item.keywords:
            item.add_marker(skip)
