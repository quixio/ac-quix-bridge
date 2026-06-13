"""Shared helpers for Quix.AI setup scripts.

Reads env vars:
  QUIX_PORTAL_API   - e.g. https://portal-api.platform.quix.io
  QUIX_TOKEN        - personal access token
  QUIX_WORKSPACE_ID - workspace id (optional for some endpoints)

Values can be set either in the shell environment OR in a `.env` file under
`quix-ai-config/`. Set `QUIX_ENV` to pick a per-environment file
(`QUIX_ENV=byox` -> `.env.byox`, `QUIX_ENV=dev` -> `.env.dev`); unset
falls back to `.env`. The selected file is loaded at module import; shell env
wins on conflict. The same file is also used to persist generated IDs
(agent_id, kb_id, ...) so subsequent scripts chain automatically — each
environment keeps its own ids.
"""

from __future__ import annotations

import os
import pathlib
import re
import time

import httpx
from dotenv import load_dotenv

# Select the .env file via QUIX_ENV (e.g. QUIX_ENV=byox -> .env.byox); unset -> .env.
_ENV_NAME = os.environ.get("QUIX_ENV", "").strip()
ENV_FILE = pathlib.Path(__file__).resolve().parent.parent / (
    f".env.{_ENV_NAME}" if _ENV_NAME else ".env"
)

# Load the selected .env without overriding existing shell values.
load_dotenv(ENV_FILE, override=False)


def active_env() -> str:
    """Name of the selected environment ('default' when QUIX_ENV is unset)."""
    return _ENV_NAME or "default"


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


def ca_verify() -> str | bool:
    """TLS verify setting for httpx. Point QUIX_CA_BUNDLE at a CA bundle for
    self-signed clusters (e.g. BYOX); falls back to REQUESTS_CA_BUNDLE /
    SSL_CERT_FILE, else default verification. httpx ignores SSL_CERT_FILE on
    its own, so we pass it explicitly."""
    return (
        os.environ.get("QUIX_CA_BUNDLE")
        or os.environ.get("REQUESTS_CA_BUNDLE")
        or os.environ.get("SSL_CERT_FILE")
        or True
    )


def http_client() -> httpx.Client:
    return httpx.Client(
        base_url=portal(), headers=headers(), timeout=60.0, verify=ca_verify()
    )


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


# --- KB resource upload helper, shared by update_kb.py -------------------- #


def upload_kb_resource(kb_id: str, md_path: pathlib.Path) -> None:
    """Replace prior resource with same filename, upload, trigger reprocess, poll."""
    target_filename = md_path.name

    with http_client() as client:
        resources = client.get(f"/api/org/knowledge-bases/{kb_id}/resources").json()
        prior = next((r for r in resources if r.get("fileName") == target_filename), None)
        if prior:
            print(f"  deleting prior resource {prior['id']} ({target_filename})")
            client.delete(
                f"/api/org/knowledge-bases/{kb_id}/resources/{prior['id']}"
            ).raise_for_status()

    # Multipart upload needs different content-type — separate client.
    with httpx.Client(
        base_url=portal(),
        headers={"Authorization": headers()["Authorization"]},
        timeout=60.0,
        verify=ca_verify(),
    ) as upload_client:
        files = {"file": (target_filename, md_path.read_bytes(), "text/markdown")}
        r = upload_client.post(f"/api/org/knowledge-bases/{kb_id}/resources", files=files)
        r.raise_for_status()
        print(f"  uploaded {target_filename} ({md_path.stat().st_size:,} bytes)")

    with http_client() as client:
        r = client.post(f"/ai/api/org/knowledge-bases/{kb_id}/process")
        if r.status_code not in (200, 202, 204):
            raise SystemExit(f"process trigger failed: {r.status_code} {r.text[:200]}")
        print(f"  reprocess triggered ({r.status_code})")

    _wait_for_processing(kb_id)


def _wait_for_processing(kb_id: str, timeout_s: float = 120.0) -> None:
    """Poll /ai/api/processing/{kb_id} until processingStatus == 'completed'."""
    t0 = time.perf_counter()
    with httpx.Client(
        base_url=portal(), headers=headers(), timeout=30.0, verify=ca_verify()
    ) as client:
        while time.perf_counter() - t0 < timeout_s:
            r = client.get(f"/ai/api/processing/{kb_id}")
            r.raise_for_status()
            data = r.json()
            status = data.get("processingStatus")
            print(f"  [{time.perf_counter() - t0:5.1f}s] {status} (tokens={data.get('contentTokenEstimate')})")
            if status == "completed":
                return
            if status in ("failed", "error"):
                raise SystemExit(f"processing failed: {data.get('failureReason')}")
            time.sleep(3)
    raise SystemExit("timed out waiting for KB processing")
