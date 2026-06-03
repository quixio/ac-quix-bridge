import logging
import os
import time
from pathlib import Path

from quixstreams import Application
from video_source import ACVideoSource

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper())


def main():
    # Unique consumer group per process so any side-channel consumers we
    # spawn get a fresh view (e.g. compacted ac-telemetry-session) on every
    # restart — see ACVideoSource._start_session_tracker_thread.
    consumer_group = f"ac_video_streaming_{os.getpid()}_{int(time.time() * 1000)}"

    app = Application(
        consumer_group=consumer_group,
        auto_create_topics=True,
    )

    output_topic = app.topic(name=os.environ.get("output", "ac-video-frames"))

    # Register the session topic so QuixStreams ensures it exists before the
    # Source subprocess tries to subscribe. We don't consume it here — the
    # Source runs its own consumer thread in its child process (SessionTracker
    # holds a threading.Lock that can't cross the process boundary).
    session_topic_name = os.environ.get("session_input", "ac-telemetry-session")
    app.topic(name=session_topic_name)

    source = ACVideoSource(name="ac-video-source")

    app.add_source(source=source, topic=output_topic)
    app.run()


if __name__ == "__main__":
    main()
