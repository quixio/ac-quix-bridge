"""Entry point for the dummy telemetry replay source.

Wires a DummyReplaySource into a QuixStreams Application that produces to the
raw and session topics. Configuration comes from the environment (selected via
ENV_FILE → .env_byox, mirroring the repo convention).
"""

import logging
import os

from dotenv import load_dotenv

# ENV_FILE selects the dotenv file (e.g. .env_byox). load_dotenv(None) falls back
# to a local .env if ENV_FILE is unset. override=True so the chosen file wins over
# any pre-existing shell vars, matching the rest of the repo's sim-PC entrypoints.
load_dotenv(os.environ.get("ENV_FILE") or None, override=True)

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper())
logger = logging.getLogger(__name__)

_HERE = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_CORPUS = os.path.join(_HERE, "data", "replay_corpus.jsonl.gz")
_DEFAULT_SESSION_TEMPLATE = os.path.join(_HERE, "data", "session_template.json")


def _maybe_disable_tls() -> None:
    """Disable TLS verification ONLY for local runs against byox self-signed certs.

    Gated behind QUIX_INSECURE_TLS=true and OFF by default. In Quix Cloud the
    broker/portal cert chain is valid, so this must never run there. The byox
    portal (https://portal-api.edge.byox.demo) serves a self-signed cert that
    httpx rejects, so we monkeypatch httpx.Client to default verify=False.
    """
    if os.environ.get("QUIX_INSECURE_TLS", "").lower() != "true":
        return

    import httpx

    logger.warning(
        "QUIX_INSECURE_TLS=true: disabling httpx TLS verification "
        "(local byox self-signed certs only)."
    )
    _orig_init = httpx.Client.__init__

    def _insecure_init(self, *args, **kwargs):
        kwargs.setdefault("verify", False)
        return _orig_init(self, *args, **kwargs)

    httpx.Client.__init__ = _insecure_init


def main():
    _maybe_disable_tls()

    # Imported after load_dotenv so module import never depends on env state.
    from quixstreams import Application

    from dummy_source import DummyReplaySource

    output = os.environ.get("output", "ac-telemetry-raw")
    session_output = os.environ.get("session_output", "ac-telemetry-session")
    speedup = float(os.environ.get("SPEEDUP", "10"))
    hostname = os.environ.get("DUMMY_HOSTNAME", "QUIX-GAMING")
    loop = os.environ.get("LOOP", "true").lower() == "true"
    max_messages = int(os.environ.get("MAX_MESSAGES", "0"))
    base_best_ms = int(os.environ.get("BASE_BEST_MS", "165000"))
    max_best_delta_ms = int(os.environ.get("MAX_BEST_DELTA_MS", "20000"))
    max_lap_offset_ms = int(os.environ.get("MAX_LAP_OFFSET_MS", "20000"))
    corpus_path = os.environ.get("CORPUS_PATH", _DEFAULT_CORPUS)
    session_template_path = os.environ.get(
        "SESSION_TEMPLATE_PATH", _DEFAULT_SESSION_TEMPLATE
    )

    # broker_address=None => Quix auto-connect via Quix__Sdk__Token.
    app = Application(
        broker_address=None,
        consumer_group="dummy-telemetry-source",
        auto_offset_reset="latest",
        auto_create_topics=True,
    )
    output_topic = app.topic(
        name=output, value_serializer="json", key_serializer="str"
    )
    session_topic = app.topic(
        name=session_output, value_serializer="json", key_serializer="str"
    )

    source = DummyReplaySource(
        name="dummy-telemetry-source",
        session_topic=session_topic,
        corpus_path=corpus_path,
        session_template_path=session_template_path,
        speedup=speedup,
        hostname=hostname,
        loop=loop,
        max_messages=max_messages,
        base_best_ms=base_best_ms,
        max_best_delta_ms=max_best_delta_ms,
        max_lap_offset_ms=max_lap_offset_ms,
    )

    app.add_source(source=source, topic=output_topic)
    app.run()


if __name__ == "__main__":
    main()
