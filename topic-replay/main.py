"""CLI entry for the topic-replay service.

Two subcommands:

  * `capture --out <dir> [--topics raw,session,config]`
        Subscribe to the three live Kafka topics and append every message
        verbatim to JSONL files under `<dir>`. Press Ctrl-C to stop.

  * `replay --src <dir> [--dry-run] [--speed FLOAT] [--target-hostname STR]`
        Load the JSONL files, detect one complete lap, then produce
        session + config once and loop the raw lap forever.

The `MODE` env var (`capture` or `replay`) is honoured when no explicit
subcommand is given on the command line — this lets the Quix deployment
flip between modes without changing the container command. CLI subcommand
ALWAYS wins over the env var when both are present.

Quix runtime env vars `Quix__Sdk__Token` and `Quix__Portal__Api` are read
implicitly by `quixstreams.Application`; this module never references them
directly.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from capture import ALL_TOPICS, run_capture
from replay import run_replay

# Load .env from CWD first (typical), then fall back to repo root so this
# script Just Works when invoked from either location.
load_dotenv()
_repo_root_env = Path(__file__).resolve().parent.parent / ".env"
if _repo_root_env.exists():
    load_dotenv(_repo_root_env, override=False)

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)-7s %(name)s :: %(message)s",
)
logger = logging.getLogger(__name__)


def _parse_topics(value: str) -> tuple[str, ...]:
    """Map a comma-separated short list (`raw,session,config`) to the
    fully-qualified topic names. Unknown shorthands raise."""
    mapping = {
        "raw": "ac-telemetry-raw",
        "session": "ac-telemetry-session",
        "config": "ac-telemetry-config",
    }
    out: list[str] = []
    for shorthand in (v.strip() for v in value.split(",")):
        if not shorthand:
            continue
        if shorthand not in mapping:
            raise argparse.ArgumentTypeError(
                f"Unknown topic shorthand {shorthand!r}; expected one of "
                f"{sorted(mapping)}"
            )
        out.append(mapping[shorthand])
    if not out:
        raise argparse.ArgumentTypeError("Empty --topics list")
    return tuple(out)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="topic-replay",
        description="Capture or replay AC telemetry Kafka topics.",
    )
    sub = parser.add_subparsers(dest="command")

    capture_p = sub.add_parser(
        "capture",
        help="Capture live Kafka topics to JSONL.",
    )
    capture_p.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output directory for JSONL files. Defaults to $CAPTURE_DIR.",
    )
    capture_p.add_argument(
        "--topics",
        type=_parse_topics,
        default=ALL_TOPICS,
        help="Comma-separated shorthands: raw,session,config (default: all).",
    )
    capture_p.add_argument(
        "--offset",
        choices=("latest", "earliest"),
        default="latest",
        help="Kafka auto.offset.reset. Use 'earliest' to drain retained history.",
    )
    capture_p.add_argument(
        "--idle-timeout",
        type=float,
        default=None,
        help="Stop capture after N seconds of no incoming messages.",
    )

    replay_p = sub.add_parser(
        "replay",
        help="Replay one detected lap from JSONL on infinite loop.",
    )
    replay_p.add_argument(
        "--src",
        type=Path,
        default=None,
        help="Source directory containing the JSONL files. Defaults to $CAPTURE_DIR.",
    )
    replay_p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print detected lap window and exit; do not produce.",
    )
    replay_p.add_argument(
        "--speed",
        type=float,
        default=None,
        help="Replay speed multiplier. Defaults to $LAP_LOOP_SPEED (or 1.0).",
    )
    replay_p.add_argument(
        "--target-hostname",
        type=str,
        default=None,
        help=(
            "Rewrite raw + session Kafka keys to this hostname. Defaults to "
            "$TARGET_HOSTNAME_OVERRIDE."
        ),
    )

    return parser


def _resolve_capture_dir(cli_value: Path | None) -> Path:
    """Pick the capture dir from CLI flag, then $CAPTURE_DIR. Error if neither."""
    if cli_value is not None:
        return cli_value
    env_value = os.environ.get("CAPTURE_DIR")
    if env_value:
        return Path(env_value)
    raise SystemExit(
        "Missing capture directory: pass --out / --src, or set $CAPTURE_DIR."
    )


def _resolve_speed(cli_value: float | None) -> float:
    if cli_value is not None:
        return cli_value
    env_value = os.environ.get("LAP_LOOP_SPEED")
    if env_value:
        try:
            return float(env_value)
        except ValueError as exc:
            raise SystemExit(f"Invalid LAP_LOOP_SPEED={env_value!r}") from exc
    return 1.0


def _resolve_target_hostname(cli_value: str | None) -> str | None:
    if cli_value is not None and cli_value != "":
        return cli_value
    env_value = os.environ.get("TARGET_HOSTNAME_OVERRIDE")
    if env_value:
        return env_value
    return None


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    argv = sys.argv[1:] if argv is None else argv

    # MODE env-var fallback: if no subcommand is on the command line, derive
    # one from MODE so the Quix deployment can flip modes by env alone.
    if not argv or argv[0] not in {"capture", "replay", "-h", "--help"}:
        mode = os.environ.get("MODE", "").strip().lower()
        if mode in {"capture", "replay"}:
            argv = [mode, *argv]

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 2

    if args.command == "capture":
        out_dir = _resolve_capture_dir(args.out)
        logger.info(
            "Capture starting; out_dir=%s topics=%s offset=%s idle_timeout=%s",
            out_dir,
            args.topics,
            args.offset,
            args.idle_timeout,
        )
        run_capture(
            out_dir,
            topics=args.topics,
            offset=args.offset,
            idle_timeout_s=args.idle_timeout,
        )
        return 0

    if args.command == "replay":
        src_dir = _resolve_capture_dir(args.src)
        speed = _resolve_speed(args.speed)
        override = _resolve_target_hostname(args.target_hostname)
        logger.info(
            "Replay starting; src_dir=%s dry_run=%s speed=%s override=%s",
            src_dir,
            args.dry_run,
            speed,
            override,
        )
        run_replay(
            src_dir,
            dry_run=args.dry_run,
            speed=speed,
            target_hostname_override=override,
        )
        return 0

    parser.error(f"Unknown command: {args.command}")
    return 2  # unreachable; argparse.error exits


if __name__ == "__main__":
    raise SystemExit(main())
