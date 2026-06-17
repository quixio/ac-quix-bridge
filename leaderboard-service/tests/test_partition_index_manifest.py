"""Enumeration builds groups from the catalog `/manifest` metadata, never SQL.

Regression for the byox timeout: the old primary path ran a per-environment
`SELECT ... GROUP BY` against the query API and hit the 30 s client timeout,
so `enumerate_groups()` served an empty group list. The fix reads the Iceberg
catalog `/manifest` once and dedupes `partition_values` in Python.

These tests stub the manifest HTTP response (via `httpx.MockTransport`) and
assert the resulting group tuples — and crucially that `LakehouseClient.query`
(the slow `GROUP BY`) is never invoked when a catalog is configured.
"""

from __future__ import annotations

import os

import httpx
import pytest

os.environ.setdefault("MONGO_USER", "test")
os.environ.setdefault("MONGO_PASSWORD", "test")
os.environ.setdefault("Quix__Workspace__Id", "test-ws")
os.environ.setdefault("Quix__Sdk__Token", "test-token")
os.environ.setdefault("CONFIG_API_URL", "http://localhost:8001")

from api import partition_index  # noqa: E402
from api.lakehouse_client import LakehouseClient  # noqa: E402
from api.settings import get_settings  # noqa: E402

# One file per (env, track, car, experiment, lap). Two files share the same
# group on different laps → must dedupe to one group. `lap=0` is the
# out-lap (dropped). A row missing `experiment` is dropped by the field guard.
_MANIFEST_ENTRIES = [
    {
        "partition_values": {
            "environment": "track-day",
            "track": "ks_nurburgring",
            "carModel": "ferrari_488",
            "experiment": "baseline",
            "lap": "1",
        }
    },
    {
        "partition_values": {
            "environment": "track-day",
            "track": "ks_nurburgring",
            "carModel": "ferrari_488",
            "experiment": "baseline",
            "lap": "2",
        }
    },
    {
        "partition_values": {
            "environment": "track-day",
            "track": "ks_nurburgring",
            "carModel": "porsche_911",
            "experiment": "soft-tyre",
            "lap": "3",
        }
    },
    {
        # out-lap only — no completed lap → dropped.
        "partition_values": {
            "environment": "track-day",
            "track": "spa",
            "carModel": "ferrari_488",
            "experiment": "baseline",
            "lap": "0",
        }
    },
    {
        # missing experiment → dropped by the field guard.
        "partition_values": {
            "environment": "track-day",
            "track": "spa",
            "carModel": "ferrari_488",
            "lap": "1",
        }
    },
]


@pytest.fixture
def catalog_settings(monkeypatch):
    """Point settings at a (fake) catalog and reset the module TTL cache."""
    settings = get_settings()
    monkeypatch.setattr(settings, "lakehouse_catalog_url", "https://catalog.test")
    monkeypatch.setattr(settings, "lakehouse_catalog_token", "tok")
    monkeypatch.setattr(settings, "lake_table", "ac_telemetry")
    monkeypatch.setattr(partition_index, "_cached_groups", None)
    monkeypatch.setattr(partition_index, "_cached_at_monotonic", 0.0)
    monkeypatch.setattr(partition_index, "_failed_at_monotonic", None)
    return settings


def _install_manifest_stub(monkeypatch, entries):
    """Patch httpx.Client so any GET returns the given manifest entries."""
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"entries": entries})

    transport = httpx.MockTransport(handler)
    real_client = httpx.Client

    def fake_client(*args, **kwargs):
        kwargs.pop("verify", None)
        return real_client(transport=transport)

    monkeypatch.setattr(partition_index.httpx, "Client", fake_client)
    return captured


def test_manifest_path_builds_groups_without_group_by(
    catalog_settings, monkeypatch
):
    captured = _install_manifest_stub(monkeypatch, _MANIFEST_ENTRIES)

    # Hard guard: the slow GROUP BY must never run on the manifest path.
    def _boom(self, sql):  # noqa: ANN001
        raise AssertionError(f"GROUP BY query ran on the manifest path: {sql!r}")

    monkeypatch.setattr(LakehouseClient, "query", _boom)

    groups = partition_index.enumerate_groups()

    assert sorted(groups) == sorted(
        [
            ("ks_nurburgring", "ferrari_488", "baseline", "track-day"),
            ("ks_nurburgring", "porsche_911", "soft-tyre", "track-day"),
        ]
    )
    # Hit the catalog manifest endpoint, not /partitions or /query.
    assert "/namespaces/default/tables/ac_telemetry/manifest" in captured["url"]


def test_manifest_path_dedupes_and_drops_outlaps(catalog_settings, monkeypatch):
    _install_manifest_stub(monkeypatch, _MANIFEST_ENTRIES)
    monkeypatch.setattr(
        LakehouseClient, "query", lambda self, sql: pytest.fail("query called")
    )

    groups = partition_index.enumerate_groups()

    # Two ks_nurburgring/ferrari_488 files (laps 1+2) collapse to one group;
    # the spa out-lap (lap=0) and the experiment-less spa file are dropped.
    assert len(groups) == 2
    assert ("spa", "ferrari_488", "baseline", "track-day") not in groups


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
