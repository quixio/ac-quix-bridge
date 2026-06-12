"""
ac_video_streaming — recording-only entrypoint.

Captures AC gameplay, records per-lap MP4s, uploads to blob storage, and
consumes ac-telemetry-session to adopt the canonical session_id. No live
JPEG streaming, so no Kafka output topic is required at startup.
"""

import logging
import os
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


def main():
    source = ACVideoSource(name="ac-video-source")
    try:
        source.run()
    except KeyboardInterrupt:
        logger.info("Interrupted — shutting down")
        source.stop()


if __name__ == "__main__":
    main()
