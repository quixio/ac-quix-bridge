"""Unit tests for Pipeline._resolve_session hostname resolution.

Covers:
1. Kafka message key is used as hostname when payload has no hostname/target_key
2. Bytes key is decoded to str before use
3. Falls back to payload "hostname" field when Kafka key is None/empty
4. Falls back to payload "target_key" field when key and "hostname" are absent
5. Final fallback to "default" when all sources are absent
"""

from __future__ import annotations

from best_laps_cache.pipeline import Pipeline


class _FakeEnrichment:
    """Captures handle_session_message calls; enrich() returns empty fields."""

    def __init__(self) -> None:
        self.session_hostnames: list[str] = []

    def handle_session_message(self, hostname: str, payload: dict) -> None:
        self.session_hostnames.append(hostname)

    def enrich(self, value: dict) -> dict:
        return {
            "environment": "",
            "experiment": "",
            "track": "",
            "carModel": "",
            "driver": "",
        }


def _pipeline(enrichment: _FakeEnrichment) -> Pipeline:
    p = Pipeline.__new__(Pipeline)
    p._enrichment = enrichment
    return p


_SESSION_PAYLOAD = {"track": "Monza", "carModel": "GT3"}


# ---------------------------------------------------------------------------
# 1. Kafka key used as hostname when payload has no hostname/target_key
# ---------------------------------------------------------------------------


def test_uses_kafka_key_as_hostname():
    enrichment = _FakeEnrichment()
    p = _pipeline(enrichment)
    p._resolve_session(_SESSION_PAYLOAD, "QUIX-GAMING", 0, [])
    assert enrichment.session_hostnames == ["QUIX-GAMING"]


# ---------------------------------------------------------------------------
# 2. Bytes key is decoded to str
# ---------------------------------------------------------------------------


def test_decodes_bytes_kafka_key():
    enrichment = _FakeEnrichment()
    p = _pipeline(enrichment)
    p._resolve_session(_SESSION_PAYLOAD, b"QUIX-GAMING", 0, [])
    assert enrichment.session_hostnames == ["QUIX-GAMING"]


# ---------------------------------------------------------------------------
# 3. Falls back to payload "hostname" when Kafka key is None
# ---------------------------------------------------------------------------


def test_falls_back_to_payload_hostname_when_key_none():
    enrichment = _FakeEnrichment()
    p = _pipeline(enrichment)
    payload = {**_SESSION_PAYLOAD, "hostname": "FROM-PAYLOAD"}
    p._resolve_session(payload, None, 0, [])
    assert enrichment.session_hostnames == ["FROM-PAYLOAD"]


def test_falls_back_to_payload_hostname_when_key_empty_string():
    enrichment = _FakeEnrichment()
    p = _pipeline(enrichment)
    payload = {**_SESSION_PAYLOAD, "hostname": "FROM-PAYLOAD"}
    p._resolve_session(payload, "", 0, [])
    assert enrichment.session_hostnames == ["FROM-PAYLOAD"]


# ---------------------------------------------------------------------------
# 4. Falls back to payload "target_key" when key and "hostname" are absent
# ---------------------------------------------------------------------------


def test_falls_back_to_payload_target_key():
    enrichment = _FakeEnrichment()
    p = _pipeline(enrichment)
    payload = {**_SESSION_PAYLOAD, "target_key": "FROM-TARGET-KEY"}
    p._resolve_session(payload, None, 0, [])
    assert enrichment.session_hostnames == ["FROM-TARGET-KEY"]


# ---------------------------------------------------------------------------
# 5. Final fallback to "default"
# ---------------------------------------------------------------------------


def test_falls_back_to_default_when_all_absent():
    enrichment = _FakeEnrichment()
    p = _pipeline(enrichment)
    p._resolve_session(_SESSION_PAYLOAD, None, 0, [])
    assert enrichment.session_hostnames == ["default"]


def test_falls_back_to_default_when_key_is_empty_bytes():
    enrichment = _FakeEnrichment()
    p = _pipeline(enrichment)
    p._resolve_session(_SESSION_PAYLOAD, b"", 0, [])
    assert enrichment.session_hostnames == ["default"]
