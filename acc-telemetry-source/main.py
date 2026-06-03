import logging
import os

from quixstreams import Application
from acc_source import AssettoCorsaCompetizioneSource, configure_logging
from quixstreams.kafka import ConnectionConfig

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper())
configure_logging()


connection = ConnectionConfig(
    bootstrap_servers=os.environ["Quix__Broker__Address"],
    security_protocol="ssl",
    enable_ssl_certificate_verification=False,
    ssl_endpoint_identification_algorithm="none",
)

def main():
    app = Application(consumer_group="acc_telemetry_source", auto_create_topics=True, broker_address=connection)
    output_topic = app.topic(name=os.environ["output"])
    session_topic = app.topic(name=os.environ.get("session_output", "acc-telemetry-session"))

    source = AssettoCorsaCompetizioneSource(
        name="acc-telemetry-source",
        session_topic=session_topic,
    )

    app.add_source(source=source, topic=output_topic)
    app.run()


if __name__ == "__main__":
    main()
