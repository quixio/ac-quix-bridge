# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx", "python-dotenv"]
# ///
"""Push a single Knowledge Base markdown file to Quix.AI.

Usage:
    uv run update_kb_resource.py path/to/file.md

KB CRUD lives at /api/org/knowledge-bases (verified via
quix-ai-exploration/probes/update_kb_resource.py). Resources are managed by
multipart POST (create) and DELETE (by id). After upload, trigger reprocess
at /ai/api/org/knowledge-bases/{id}/process.
"""

from __future__ import annotations

import pathlib
import re
import sys
import time

import httpx

from _common import headers, http_client, portal, write_env


def _slug(path: pathlib.Path) -> str:
    """KB display name derived from filename: 'analysis_contract.md' -> 'Analysis Contract'."""
    stem = path.stem
    return stem.replace("_", " ").replace("-", " ").title()


def _env_key(path: pathlib.Path) -> str:
    """Env var key: 'analysis_contract.md' -> 'ANALYSIS_CONTRACT_KB_ID'."""
    return re.sub(r"[^A-Z0-9]", "_", path.stem.upper()) + "_KB_ID"


def _wait_for_processing(kb_id: str, timeout_s: float = 120.0) -> None:
    """Poll /ai/api/processing/{kb_id} until processingStatus == 'completed'."""
    t0 = time.perf_counter()
    with httpx.Client(base_url=portal(), headers=headers(), timeout=30.0) as client:
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


def main(argv: list[str] | None = None) -> int:
    if not argv:
        argv = sys.argv[1:]
    if not argv:
        print("Usage: update_kb_resource.py <path-to-md>")
        return 2

    md_path = pathlib.Path(argv[0]).resolve()
    if not md_path.is_file():
        print(f"File not found: {md_path}")
        return 1

    name = _slug(md_path)
    target_filename = md_path.name

    with http_client() as client:
        existing_kbs = client.get("/api/org/knowledge-bases").json()
        match = next((kb for kb in existing_kbs if kb.get("name") == name), None)
        body = {"name": name, "description": f"Source: {md_path.name}"}

        if match:
            kb_id = match["id"]
            print(f"Updating existing KB {kb_id} (name={name!r})")
            client.put(f"/api/org/knowledge-bases/{kb_id}", json=body).raise_for_status()
        else:
            print(f"Creating new KB (name={name!r})")
            kb_id = client.post("/api/org/knowledge-bases", json=body).json()["id"]

        # Replace any existing resource with the same filename, then upload
        # via multipart. Mirrors quix-ai-exploration/probes/update_kb_resource.py.
        resources = client.get(f"/api/org/knowledge-bases/{kb_id}/resources").json()
        prior = next((r for r in resources if r.get("fileName") == target_filename), None)
        if prior:
            print(f"  deleting prior resource {prior['id']} ({target_filename})")
            client.delete(f"/api/org/knowledge-bases/{kb_id}/resources/{prior['id']}").raise_for_status()

    # Multipart upload needs a different content-type — separate client.
    with httpx.Client(base_url=portal(), headers={"Authorization": headers()["Authorization"]}, timeout=60.0) as upload_client:
        files = {"file": (target_filename, md_path.read_bytes(), "text/markdown")}
        r = upload_client.post(f"/api/org/knowledge-bases/{kb_id}/resources", files=files)
        r.raise_for_status()
        print(f"  uploaded {target_filename} ({md_path.stat().st_size:,} bytes)")

    # Trigger reprocess
    with http_client() as client:
        r = client.post(f"/ai/api/org/knowledge-bases/{kb_id}/process")
        if r.status_code not in (200, 202, 204):
            raise SystemExit(f"process trigger failed: {r.status_code} {r.text[:200]}")
        print(f"  reprocess triggered ({r.status_code})")

    _wait_for_processing(kb_id)

    write_env(_env_key(md_path), kb_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
