import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / "static"
ENV_FILE = ROOT.parent / "probes" / ".env"

load_dotenv(ENV_FILE)

PORTAL = os.environ["QUIX_PORTAL_API"].rstrip("/")
TOKEN = os.environ["QUIX_TOKEN"]
WORKSPACE_ID = os.environ.get("QUIX_WORKSPACE_ID", "")
WORKSPACE_NAME = os.environ.get("QUIX_WORKSPACE_NAME", "")


def portal_headers(*, streaming: bool = False) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "application/json",
    }
    if streaming:
        headers["Accept"] = "text/event-stream"
    return headers


def portal_context() -> dict[str, str]:
    return {
        "workspaceId": WORKSPACE_ID,
        "workspaceName": WORKSPACE_NAME,
        "page": f"/pipeline?workspace={WORKSPACE_ID}",
    }
