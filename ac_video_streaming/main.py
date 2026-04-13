import logging
import os
import time

from quixstreams import Application
from session_tracker import SessionTracker
from video_source import ACVideoSource

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper())


def main():
    # Unique consumer group per process so that on every restart we re-read
    # the (compacted) ac-telemetry-session topic and pick up the current
    # session_id. With a stable group + auto-commit, a restart mid-session
    # would silently miss the current session message.
    consumer_group = f"ac_video_streaming_{os.getpid()}_{int(time.time() * 1000)}"

    app = Application(
        consumer_group=consumer_group,
        auto_create_topics=True,
        auto_offset_reset="earliest",
    )

    output_topic = app.topic(name=os.environ.get("output", "ac-video-frames"))

    session_topic_name = os.environ.get("session_input", "ac-telemetry-session")
    session_topic = app.topic(name=session_topic_name)

    session_tracker = SessionTracker()
    sdf = app.dataframe(topic=session_topic)
    sdf.update(session_tracker.update_from_message)

    source = ACVideoSource(name="ac-video-source", session_tracker=session_tracker)

    app.add_source(source=source, topic=output_topic)
    app.run()


if __name__ == "__main__":
    main()
