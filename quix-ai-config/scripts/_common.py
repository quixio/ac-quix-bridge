"""Shared helpers for Quix.AI setup scripts.

Reads env vars:
  QUIX_PORTAL_API   - e.g. https://portal-api.platform.quix.io
  QUIX_TOKEN        - personal access token
  QUIX_WORKSPACE_ID - workspace id (optional for some endpoints)

Persists IDs to a local `.env` file in the script's working directory so
subsequent scripts can chain.
"""

from __future__ import annotations

import os
import pathlib
import re

import httpx

ENV_FILE = pathlib.Path(__file__).resolve().parent.parent / ".env"


def portal() -> str:
    url = os.environ.get("QUIX_PORTAL_API", "").rstrip("/")
    if not url:
        raise SystemExit("QUIX_PORTAL_API not set")
    return url


def token() -> str:
    t = os.environ.get("QUIX_TOKEN", "")
    if not t:
        raise SystemExit("QUIX_TOKEN not set")
    return t


def headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token()}",
        "Content-Type": "application/json",
    }


def http_client() -> httpx.Client:
    return httpx.Client(base_url=portal(), headers=headers(), timeout=60.0)


def write_env(key: str, value: str) -> None:
    """Append-or-update `KEY=VALUE` in the local .env file."""
    lines: list[str] = []
    if ENV_FILE.exists():
        lines = ENV_FILE.read_text().splitlines()

    pattern = re.compile(rf"^{re.escape(key)}=")
    new_lines = [line for line in lines if not pattern.match(line)]
    new_lines.append(f"{key}={value}")
    ENV_FILE.write_text("\n".join(new_lines) + "\n")
    print(f"  wrote {key}={value} -> {ENV_FILE}")


def read_env_value(key: str) -> str | None:
    """Read a value previously stashed by write_env."""
    if not ENV_FILE.exists():
        return None
    pattern = re.compile(rf"^{re.escape(key)}=(.*)$")
    for line in ENV_FILE.read_text().splitlines():
        m = pattern.match(line)
        if m:
            return m.group(1)
    return None
