import logging
import os
from pathlib import Path

from quixstreams import Application
from acc_source import AssettoCorsaCompetizioneSource, configure_logging

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

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper())
configure_logging()


def main():
    # Mode A: Application() auto-connects from Quix__Sdk__Token + Quix__Portal__Api
    # (+ Quix__Workspace__Id if the token spans workspaces). The SDK resolves broker
    # address, SASL mechanism, and credentials per workspace — so nothing is
    # hardcoded (byox uses SCRAM-SHA-512, quixdev SCRAM-SHA-256).
    app = Application(consumer_group="acc_telemetry_source", auto_create_topics=True)
    output_topic = app.topic(name=os.environ["output"])
    session_topic = app.topic(name=os.environ.get("session_output", "ac-telemetry-session"))

    source = AssettoCorsaCompetizioneSource(
        name="acc-telemetry-source",
        session_topic=session_topic,
    )

    app.add_source(source=source, topic=output_topic)
    app.run()


if __name__ == "__main__":
    main()
