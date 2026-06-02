"""Seed the mock DCM with session + experiment configs for local replay.

Run once after `docker compose -f docker-compose.dev.yml up` has brought the
mock DCM (`config-api`) to a healthy state. Reads the captured
`ac-telemetry-session.jsonl` to lift `track` / `carModel` / `playerName`
from the most recent row, then POSTs two configs keyed by
`metadata.target_key=<hostname>`:

  1. `type=session`     — mirrors what `session-config-bridge` would write.
  2. `type=experiment`  — driver + environment + experiment_id for the
                          ghost-lap consumer's DCM lookup.

Both use `replace: true`, so the script is idempotent: re-running it
overwrites any previously-seeded values for the same `(type, target_key)`
pair without creating duplicate configs.

Why this lives outside `mock_config_api/main.py`: baking the seed into the
mock's FastAPI lifespan would break the backend test suite, which expects
an empty mock at startup and clears state between tests.

CLI:
    python mock_config_api/seed_local.py \\
        --src C:/tmp/replay-2026-06-02 \\
        [--host http://localhost:8001] [--hostname XPS]

No external dependencies — stdlib only.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import urllib.error
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

# Defaults match docker-compose.dev.yml + the on-disk capture from
# 2026-06-02 (`XPS` is the sim PC's hostname).
DEFAULT_HOST = "http://localhost:8001"
DEFAULT_HOSTNAME = "XPS"
SESSION_JSONL_NAME = "ac-telemetry-session.jsonl"

# Hard-coded experiment payload — these fields drive the leaderboard's
# active-row enrichment AND key the lake query for historicals (Best Laps
# + Live Sector Comparison gate-vectors). Values must match the cloud lake's
# Hive partitions for the dev workspace so the local backend's QuixLake
# query against `quixlake-quixdev-quixlakev2-dev.deployments-dev.quix.io`
# returns the dummy-driver historicals.
EXPERIMENT_CONTENT = {
    "experiment_id": "LeaderBoard",
    "experiment": "LeaderBoard",
    "driver": "ludvík",
    "environment": "prague_office",
}


def _last_session_row(jsonl_path: Path) -> dict:
    """Return the value-dict of the last non-empty line in the session
    JSONL. Raises if the file is missing or empty."""
    if not jsonl_path.exists():
        raise FileNotFoundError(
            f"Session JSONL not found: {jsonl_path}. "
            "Run `topic-replay capture` first or check --src."
        )
    last: dict | None = None
    with jsonl_path.open("r", encoding="utf-8") as fh:
        for raw_line in fh:
            line = raw_line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            value = row.get("value")
            if isinstance(value, dict):
                last = value
    if last is None:
        raise ValueError(
            f"No session rows with dict payloads found in {jsonl_path}."
        )
    return last


def _post_json(url: str, body: dict, timeout: float = 5.0) -> int:
    """POST a JSON body. Returns the HTTP status code; raises on HTTP
    errors so the seed script exits non-zero with a clear message."""
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status


def _build_session_payload(hostname: str, session: dict) -> dict:
    """Mirror the shape `session-config-bridge` would write to DCM —
    `target_key=<hostname>`, `type=session`, content keys `track` /
    `carModel` / `playerName`."""
    return {
        "metadata": {
            "type": "session",
            "target_key": hostname,
            "category": "ac-telemetry",
        },
        "content": {
            "track": session.get("track") or "",
            "carModel": session.get("carModel") or "",
            "playerName": session.get("playerName") or "",
        },
        "replace": True,
    }


def _build_experiment_payload(hostname: str) -> dict:
    """Static experiment config — `driver` lowercase, `environment` and
    `experiment_id` cosmetic (lake partitions, not used locally)."""
    return {
        "metadata": {
            "type": "experiment",
            "target_key": hostname,
            "category": "ac-telemetry",
        },
        "content": dict(EXPERIMENT_CONTENT),
        "replace": True,
    }


def seed(src_dir: Path, host: str, hostname: str) -> None:
    """Drive the two POSTs. Raises on any failure so `main()` can
    translate to a non-zero exit code."""
    session = _last_session_row(src_dir / SESSION_JSONL_NAME)

    session_payload = _build_session_payload(hostname, session)
    experiment_payload = _build_experiment_payload(hostname)

    url = host.rstrip("/") + "/api/v1/configurations"

    logger.info(
        "Seeding mock DCM at %s for hostname=%s (track=%r car=%r player=%r)",
        url,
        hostname,
        session_payload["content"]["track"],
        session_payload["content"]["carModel"],
        session_payload["content"]["playerName"],
    )

    status = _post_json(url, session_payload)
    logger.info("session config POST → HTTP %s", status)
    status = _post_json(url, experiment_payload)
    logger.info("experiment config POST → HTTP %s", status)

    print(
        f"seeded session + experiment for hostname={hostname}",
        file=sys.stderr,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="seed_local",
        description=(
            "Seed the mock DCM with session + experiment configs so the "
            "local backend's ghost-lap consumer can enrich replay traffic."
        ),
    )
    parser.add_argument(
        "--src",
        type=Path,
        required=True,
        help=(
            "Directory containing ac-telemetry-session.jsonl from a prior "
            "topic-replay capture."
        ),
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=f"Mock DCM base URL (default: {DEFAULT_HOST}).",
    )
    parser.add_argument(
        "--hostname",
        default=DEFAULT_HOSTNAME,
        help=(
            "Hostname target_key for both configs "
            f"(default: {DEFAULT_HOSTNAME})."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level="INFO",
        format="%(asctime)s %(levelname)-7s %(name)s :: %(message)s",
    )
    args = _build_parser().parse_args(argv)
    try:
        seed(args.src, args.host, args.hostname)
    except FileNotFoundError as exc:
        print(f"seed_local: {exc}", file=sys.stderr)
        return 2
    except (urllib.error.URLError, ConnectionError) as exc:
        print(
            f"seed_local: cannot reach mock DCM at {args.host}: {exc}",
            file=sys.stderr,
        )
        return 3
    except Exception as exc:
        print(f"seed_local: unexpected error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
