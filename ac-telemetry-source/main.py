import logging
import os

from quixstreams import Application
from ac_source import AssettoCorsaSource

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "DEBUG").upper())


def main():
    app = Application(consumer_group="ac_telemetry_source", auto_create_topics=True)
    output_topic = app.topic(name=os.environ["output"])
    session_topic = app.topic(name=os.environ.get("session_output", "ac-telemetry-session"))

    source = AssettoCorsaSource(
        name="ac-telemetry-source",
        session_topic=session_topic,
    )

    app.add_source(source=source, topic=output_topic)
    app.run()


if __name__ == "__main__":
    main()
