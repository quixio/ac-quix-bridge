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
# override=True so a stale shell env can't shadow what's in the root .env.
load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=True)

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
