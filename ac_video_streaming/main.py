import logging
import os
import time
from pathlib import Path

from quixstreams import Application
from quixstreams.kafka import ConnectionConfig
from video_source import ACVideoSource

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper())


connection = ConnectionConfig(
    bootstrap_servers=os.environ["Quix__Broker__Address"],
    security_protocol="sasl_ssl",
    sasl_mechanism="SCRAM-SHA-512",
    sasl_username=os.environ["Quix__Broker__Username"],
    sasl_password=os.environ["Quix__Broker__Password"],
    enable_ssl_certificate_verification=False,
    ssl_endpoint_identification_algorithm="none",
)


def main():
    # Unique consumer group per process so any side-channel consumers we
    # spawn get a fresh view (e.g. compacted ac-telemetry-session) on every
    # restart — see ACVideoSource._start_session_tracker_thread.
    consumer_group = f"ac_video_streaming_{os.getpid()}_{int(time.time() * 1000)}"

    app = Application(
        consumer_group=consumer_group,
        auto_create_topics=False,
        broker_address=connection,
    )

    output_topic = app.topic(name=os.environ.get("VIDEO_OUTPUT_TOPIC", "ac-video-frames"))

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
