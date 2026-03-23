"""
Telemetry Enrichment — Enriches raw AC telemetry with experiment metadata
and session data from the Dynamic Configuration Manager via join_lookup.

Two config types are joined:
  - "experiment" (from the form): driver, beers, environment, test_rig, experiment_id, test_id
  - "session" (from AC static block): carModel, track, maxRpm, maxFuel
"""

import logging
import os

from quixstreams import Application
from quixstreams.dataframe.joins.lookups import QuixConfigurationService

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper())
logger = logging.getLogger(__name__)


def main():
    app = Application(
        consumer_group="telemetry_enrichment",
        auto_offset_reset="earliest",
    )

    input_topic = app.topic(name=os.environ.get("input", "ac-telemetry-raw"))
    output_topic = app.topic(name=os.environ.get("output", "ac-telemetry-enriched"))
    config_topic = app.topic(name=os.environ.get("config_topic", "ac-telemetry-config"))

    config_lookup = QuixConfigurationService(
        topic=config_topic,
        app_config=app.config,
    )

    sdf = app.dataframe(topic=input_topic)

    # Enrich with experiment config (from form)
    sdf = sdf.join_lookup(
        lookup=config_lookup,
        on=lambda value, key: key,
        fields={
            "test_id": config_lookup.json_field(jsonpath="$.test_id", type="experiment"),
            "environment": config_lookup.json_field(jsonpath="$.environment", type="experiment"),
            "test_rig": config_lookup.json_field(jsonpath="$.test_rig", type="experiment"),
            "experiment_id": config_lookup.json_field(jsonpath="$.experiment_id", type="experiment"),
            "driver": config_lookup.json_field(jsonpath="$.driver", type="experiment"),
            "beers": config_lookup.json_field(jsonpath="$.beers", type="experiment"),
        },
    )

    # Enrich with session config (from AC static block)
    sdf = sdf.join_lookup(
        lookup=config_lookup,
        on=lambda value, key: key,
        fields={
            "carModel": config_lookup.json_field(jsonpath="$.carModel", type="session"),
            "track": config_lookup.json_field(jsonpath="$.track", type="session"),
            "maxRpm": config_lookup.json_field(jsonpath="$.maxRpm", type="session"),
            "maxFuel": config_lookup.json_field(jsonpath="$.maxFuel", type="session"),
        },
    )

    # Add lap number (completedLaps=0 means lap 1 in progress)
    sdf = sdf.apply(lambda v: {**v, "lap_number": v.get("completedLaps", 0) + 1})

    sdf.to_topic(topic=output_topic)

    logger.info("Starting Telemetry Enrichment service")
    app.run()


if __name__ == "__main__":
    main()
