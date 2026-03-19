import logging
import os

from quixstreams import Application
from ac_source import AssettoCorsaSource

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "DEBUG").upper())


def main():
    app = Application(consumer_group="ac_telemetry_source", auto_create_topics=True)
    source = AssettoCorsaSource(name="ac-telemetry-source")
    output_topic = app.topic(name=os.environ["output"])

    app.add_source(source=source, topic=output_topic)
    app.run()


if __name__ == "__main__":
    main()
