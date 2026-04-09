import logging
import os

from quixstreams import Application
from video_source import ACVideoSource

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper())


def main():
    app = Application(consumer_group="ac_video_streaming", auto_create_topics=True)
    output_topic = app.topic(name=os.environ.get("output", "ac-video-frames"))

    source = ACVideoSource(name="ac-video-source")

    app.add_source(source=source, topic=output_topic)
    app.run()


if __name__ == "__main__":
    main()
