from quixstreams import Application
from ac_source import AssettoCorsaSource
import os

from dotenv import load_dotenv
load_dotenv()


def main():
    app = Application(consumer_group="ac_telemetry_source", auto_create_topics=True)
    source = AssettoCorsaSource(name="ac-telemetry-source")
    output_topic = app.topic(name=os.environ["output"])

    app.add_source(source=source, topic=output_topic)
    app.run()


if __name__ == "__main__":
    main()
