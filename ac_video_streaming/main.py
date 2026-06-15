"""
ac_video_streaming — recording-only entrypoint.

Captures AC gameplay, records per-lap MP4s, uploads to blob storage, and
consumes ac-telemetry-session to adopt the canonical session_id. No live
JPEG streaming, so no Kafka output topic is required at startup.
"""

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv

# ENV_FILE is mandatory: it selects the target environment (env/.env.byox or
# env/.env.quixdev). override=True so a stale shell env can't shadow the file.
_env_file = os.environ.get("ENV_FILE")
if not _env_file or not Path(_env_file).is_file():
    raise SystemExit(
        "ENV_FILE is not set or points to a missing file. "
        "Launch via startUpScript-acc.bat (environment selector) or set ENV_FILE "
        r"to e.g. C:\repos\ac-quix-bridge\env\.env.quixdev"
    )
load_dotenv(_env_file, override=True)

from video_source import ACVideoSource

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper())
logger = logging.getLogger(__name__)

# Tee logs to a rotating file (default logs/video-source.log next to this module,
# override with VIDEO_LOG_FILE) so recording/session history survives the terminal
# closing on the sim PC. Best-effort: a bad path/FS must not crash recording.
_video_log = Path(os.environ.get("VIDEO_LOG_FILE") or Path(__file__).resolve().parent / "logs" / "video-source.log")
_root = logging.getLogger()
if not any(isinstance(h, RotatingFileHandler) for h in _root.handlers):
    try:
        _video_log.parent.mkdir(parents=True, exist_ok=True)
        _fh = RotatingFileHandler(_video_log, maxBytes=10_000_000, backupCount=5)
        _fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        _root.addHandler(_fh)
        logger.info("File logging enabled: %s (rotating 10MB x5)", _video_log)
    except OSError as exc:
        logging.warning("File logging disabled: %s", exc)


def main():
    source = ACVideoSource(name="ac-video-source")
    try:
        source.run()
    except KeyboardInterrupt:
        logger.info("Interrupted — shutting down")
        source.stop()


if __name__ == "__main__":
    main()
