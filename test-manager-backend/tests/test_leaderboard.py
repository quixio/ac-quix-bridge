"""
Pytest tests for the Leaderboard endpoint (`/api/v1/leaderboard/best-laps`).

# TODO(sc-71954 r3): tests need rewrite after QuixLake direct-call refactor.
# The endpoint now reads `settings.quixlake_url` / `settings.quix_lake_token`
# directly (no more `get_measurements_url_base` / `_FALLBACK_MEASUREMENTS_URL`
# / `get_effective_integration_settings`). The fixtures below were written
# against the old fall-through and do not exercise the new code path. Import
# must still succeed so `pytest --collect-only` doesn't ImportError, but the
# tests themselves are stale until rewritten.

Scope: spec §6.2 — backend tests.

Boundary mocks (per spec §6.2):
- HTTP boundary to `{measurements_url}/api/query` is mocked via a monkeypatched
  `httpx.AsyncClient`. The real SQL engine (DuckDB) is not exercised; we assert
  the outgoing SQL *shape* and stub the CSV response shape directly.
- Mongo `drivers` collection is mocked with a minimal fake database object so
  the tests don't need testcontainers / Docker.

Every test docstring cites the spec / architecture section it validates.
"""
from __future__ import annotations

import os
import re
from typing import Any, Callable, Generator
from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Env-var priming. The real `Settings` object (via pydantic-settings) reads
# required fields from the environment at construction time. We don't want
# these tests to require a live Mongo or a real SDK token, so we pre-seed
# the env with throwaway values BEFORE the Settings class is first imported.
# ---------------------------------------------------------------------------
os.environ.setdefault("MONGO_USER", "test")
os.environ.setdefault("MONGO_PASSWORD", "test")
os.environ.setdefault("MONGO_HOST", "localhost")
os.environ.setdefault("MONGO_PORT", "27017")
os.environ.setdefault("MONGO_DATABASE", "test")
os.environ.setdefault("Quix__Workspace__Id", "test-ws")
os.environ.setdefault("Quix__Sdk__Token", "test-sdk-token")
os.environ.setdefault("CONFIG_API_URL", "http://test-config-api")
os.environ.setdefault("API_AUTH_ACTIVE", "false")

from api.auth import read_permission  # noqa: E402
from api.models import (  # noqa: E402
    BestLapEntry,
    DeploymentReference,
    IntegrationSettings,
)
from api.mongo import get_mongo  # noqa: E402
from api.routes.leaderboard import router as leaderboard_router, _BEST_LAPS_SQL  # noqa: E402
from api.settings import Settings, get_settings  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class FakeDriversCollection:
    """Minimal stand-in for pymongo's collection — just enough for the
    `drivers.find({}, {"name": 1})` call inside `_build_driver_name_lookup`.
    """

    def __init__(self, docs: list[dict[str, Any]]):
        self._docs = docs

    def find(self, _filter: dict[str, Any], _projection: dict[str, Any] | None = None):
        return iter(self._docs)


class FakeMongo:
    """Minimal stand-in for the pymongo `Database` object. Only `.drivers` is
    accessed by `leaderboard.py`, so the surface area is tiny.
    """

    def __init__(self, driver_docs: list[dict[str, Any]] | None = None):
        self.drivers = FakeDriversCollection(driver_docs or [])


def _make_settings(**overrides: Any) -> Settings:
    """Build a minimal Settings object without relying on .env."""
    defaults = dict(
        api_auth_active=False,
        workspace_id="test-ws",
        sdk_token="test-sdk-token",
        config_api_url="http://test-config-api",
        secret_key="x" * 64,
    )
    defaults.update(overrides)
    return Settings.model_construct(**defaults)


def _make_integration_settings(
    measurements_public_url: str | None = "http://fake-measurements.local",
) -> IntegrationSettings:
    """Build an IntegrationSettings with an optional measurements deployment."""
    if measurements_public_url is None:
        return IntegrationSettings()
    return IntegrationSettings(
        measurements_deployment=DeploymentReference(
            deployment_id="dep-1",
            workspace_id="ws-1",
            deployment_name="Query UI",
            public_url=measurements_public_url,
        )
    )


@pytest.fixture
def app_with_overrides() -> Callable[..., tuple[FastAPI, TestClient, dict]]:
    """Factory that builds a minimal FastAPI app with the leaderboard router
    mounted and all external dependencies stubbed.

    Returns `(app, client, captured)` where `captured` is a dict the test can
    inspect for post-hoc assertions (e.g. outgoing request body, headers).
    """

    def _make(
        *,
        driver_docs: list[dict[str, Any]] | None = None,
        measurements_url: str | None = "http://fake-measurements.local",
        csv_response: str = "",
        response_status: int = 200,
        raise_exc: Exception | None = None,
    ) -> tuple[FastAPI, TestClient, dict]:
        app = FastAPI()
        app.include_router(leaderboard_router, prefix="/api/v1")

        captured: dict[str, Any] = {"request_body": None, "request_headers": None, "request_url": None}

        # --- dep overrides ---
        app.dependency_overrides[read_permission] = lambda: None
        app.dependency_overrides[get_mongo] = lambda: FakeMongo(driver_docs=driver_docs)

        # --- monkeypatch module-level helpers on api.routes.leaderboard ---
        settings_patch = patch(
            "api.routes.leaderboard.get_settings",
            return_value=_make_settings(),
        )
        integration_patch = patch(
            "api.routes.leaderboard.get_effective_integration_settings",
            return_value=_make_integration_settings(
                measurements_public_url=measurements_url
            ),
        )

        # --- stub httpx.AsyncClient ---
        class _FakeResponse:
            def __init__(self, status: int, text: str):
                self.status_code = status
                self.text = text
                self.is_success = 200 <= status < 300

        class _FakeAsyncClient:
            def __init__(self, *a: Any, **kw: Any) -> None:
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a: Any) -> None:
                return None

            async def post(self, url: str, *, content: str, headers: dict, timeout: float):
                captured["request_url"] = url
                captured["request_body"] = content
                captured["request_headers"] = headers
                captured["request_timeout"] = timeout
                if raise_exc is not None:
                    raise raise_exc
                return _FakeResponse(response_status, csv_response)

        httpx_patch = patch(
            "api.routes.leaderboard.httpx.AsyncClient",
            _FakeAsyncClient,
        )

        settings_patch.start()
        integration_patch.start()
        httpx_patch.start()

        client = TestClient(app)
        # Stash patches so the caller can stop them in teardown.
        captured["_patches"] = [settings_patch, integration_patch, httpx_patch]
        return app, client, captured

    made: list[dict] = []
    yield lambda **kw: (lambda tup: (made.append(tup[2]), tup)[1])(_make(**kw))

    for captured in made:
        for p in captured.get("_patches", []):
            p.stop()


# ---------------------------------------------------------------------------
# SQL shape tests (spec §6.2: "Verify the SQL shape")
# ---------------------------------------------------------------------------


def test_sql_contains_lap_gt_1_filter(app_with_overrides: Callable) -> None:
    """Validates spec §5.2 / §6.2: the query must filter `lap > 1` to exclude
    the out-lap. This is one of the two core WHERE/HAVING predicates.
    """
    _, client, captured = app_with_overrides(csv_response="")

    resp = client.get("/api/v1/leaderboard/best-laps")
    assert resp.status_code == 200

    body = captured["request_body"]
    assert body is not None, "no outgoing request captured"
    assert "lap > 1" in body, (
        f"SQL missing `lap > 1` filter. body=\n{body}"
    )


def test_sql_contains_having_max_ilasttime_gt_0(app_with_overrides: Callable) -> None:
    """Validates spec §5.2 / §6.2: HAVING MAX(iLastTime) > 0 filters
    stale / zero-initialised rows.
    """
    _, client, captured = app_with_overrides(csv_response="")
    resp = client.get("/api/v1/leaderboard/best-laps")
    assert resp.status_code == 200

    body = captured["request_body"].lower()
    # Accept either literal ordering around the MAX() call, but require the
    # HAVING predicate to reference iLastTime > 0.
    assert "having" in body, "SQL has no HAVING clause"
    assert "max(ilasttime)" in body.replace(" ", ""), "HAVING MAX(iLastTime) missing"
    # Require the > 0 comparison somewhere in the HAVING clause body.
    having_segment = body.split("having", 1)[1]
    assert "> 0" in having_segment.replace(" ", "") or ">0" in having_segment.replace(" ", ""), (
        "HAVING clause does not compare > 0"
    )


def test_sql_group_by_covers_required_columns(app_with_overrides: Callable) -> None:
    """Validates spec §5.2: inner CTE GROUPs BY
    (track, carModel, experiment, driver, session_id, lap); outer SELECT
    GROUPs BY (track, carModel, experiment, driver).
    """
    _, client, captured = app_with_overrides(csv_response="")
    resp = client.get("/api/v1/leaderboard/best-laps")
    assert resp.status_code == 200
    body = captured["request_body"]

    # Inner CTE group-by (has session_id + lap)
    inner_group_pattern = re.compile(
        r"GROUP BY\s+track\s*,\s*carModel\s*,\s*experiment\s*,\s*driver\s*,\s*session_id\s*,\s*lap",
        re.IGNORECASE,
    )
    assert inner_group_pattern.search(body), (
        f"Inner CTE GROUP BY missing required columns. body=\n{body}"
    )

    # Outer SELECT group-by (no session_id / lap)
    outer_group_pattern = re.compile(
        r"GROUP BY\s+track\s*,\s*carModel\s*,\s*experiment\s*,\s*driver(?!\s*,\s*session_id)",
        re.IGNORECASE,
    )
    assert outer_group_pattern.search(body), (
        f"Outer GROUP BY missing or wrong columns. body=\n{body}"
    )


def test_sql_selects_from_ac_telemetry(app_with_overrides: Callable) -> None:
    """Validates arch doc §3 + spec §5.2: the FROM target is `ac_telemetry`."""
    _, client, captured = app_with_overrides(csv_response="")
    resp = client.get("/api/v1/leaderboard/best-laps")
    assert resp.status_code == 200
    assert re.search(r"FROM\s+ac_telemetry", captured["request_body"], re.IGNORECASE), (
        f"SQL does not SELECT FROM ac_telemetry. body=\n{captured['request_body']}"
    )


def test_sql_static_constant_matches_module(app_with_overrides: Callable) -> None:
    """Validates arch doc §4: `_BEST_LAPS_SQL` is a static module-level constant
    and is the exact string the endpoint sends upstream (no user-controlled
    interpolation, no injection surface).
    """
    _, client, captured = app_with_overrides(csv_response="")
    resp = client.get("/api/v1/leaderboard/best-laps")
    assert resp.status_code == 200
    assert captured["request_body"] == _BEST_LAPS_SQL, (
        "Outgoing SQL is not the module-level _BEST_LAPS_SQL constant."
    )


def test_request_uses_bearer_token_and_text_plain(app_with_overrides: Callable) -> None:
    """Validates arch doc §3: the upstream call carries `Authorization: Bearer
    <sdk_token>` and `Content-Type: text/plain`.
    """
    _, client, captured = app_with_overrides(csv_response="")
    resp = client.get("/api/v1/leaderboard/best-laps")
    assert resp.status_code == 200
    headers = captured["request_headers"] or {}
    assert headers.get("Authorization") == "Bearer test-sdk-token"
    assert headers.get("Content-Type") == "text/plain"


def test_request_hits_api_query_path(app_with_overrides: Callable) -> None:
    """Validates arch doc §3: POST target is `{measurements_url}/api/query`."""
    _, client, captured = app_with_overrides(
        csv_response="",
        measurements_url="http://lake-host:9999",
    )
    resp = client.get("/api/v1/leaderboard/best-laps")
    assert resp.status_code == 200
    assert captured["request_url"] == "http://lake-host:9999/api/query"


# ---------------------------------------------------------------------------
# Response-shape tests (spec §6.2 + §7.1)
# ---------------------------------------------------------------------------


_SAMPLE_CSV = (
    "track,carModel,experiment,driver,best_lap_ms\r\n"
    "ks_nurburgring,bmw_1m,exp_42,ludvik,98342\r\n"
    "ks_nurburgring,bmw_1m,exp_42,alice,99108\r\n"
    "silverstone,ferrari_458,exp_17,bob,101500\r\n"
)


def test_empty_lake_returns_empty_list(app_with_overrides: Callable) -> None:
    """Validates spec §6.2 ("empty lake response → []") and S6 empty-state:
    zero rows from the lake must yield 200 + `[]`, NOT 500.
    """
    _, client, _ = app_with_overrides(
        # Header-only CSV, no data rows.
        csv_response="track,carModel,experiment,driver,best_lap_ms\r\n"
    )
    resp = client.get("/api/v1/leaderboard/best-laps")
    assert resp.status_code == 200
    assert resp.json() == []


def test_empty_string_body_returns_empty_list(app_with_overrides: Callable) -> None:
    """Validates arch doc §3 step 3: a completely empty body is handled
    gracefully (no parse errors) and yields 200 + [].
    """
    _, client, _ = app_with_overrides(csv_response="")
    resp = client.get("/api/v1/leaderboard/best-laps")
    assert resp.status_code == 200
    assert resp.json() == []


def test_response_shape_matches_best_lap_entry_schema(
    app_with_overrides: Callable,
) -> None:
    """Validates spec §7.1 example: every response row must contain the V1
    required fields plus the V2-reserved optional fields (null in V1).
    """
    _, client, _ = app_with_overrides(
        csv_response=_SAMPLE_CSV,
        driver_docs=[{"name": "Ludvík"}, {"name": "Alice"}, {"name": "Bob"}],
    )
    resp = client.get("/api/v1/leaderboard/best-laps")
    assert resp.status_code == 200
    rows = resp.json()
    assert isinstance(rows, list) and rows, "response must be a non-empty list"

    required = {"track", "car", "experiment", "driver", "best_lap_ms"}
    optional_v2 = {"session_id", "lap_number", "achieved_at"}
    for row in rows:
        # V1 required fields
        missing = required - row.keys()
        assert not missing, f"row {row} missing required fields: {missing}"
        # best_lap_ms must be an int
        assert isinstance(row["best_lap_ms"], int), (
            f"best_lap_ms should be int, got {type(row['best_lap_ms']).__name__} "
            f"in row {row}"
        )
        # V2 reserved fields must all be present AND null
        for k in optional_v2:
            assert k in row, f"row {row} missing V2-reserved field '{k}'"
            assert row[k] is None, (
                f"V2-reserved field '{k}' must default to null in V1, got {row[k]!r}"
            )


def test_carmodel_is_renamed_to_car(app_with_overrides: Callable) -> None:
    """Validates spec §7.1 + arch doc §3 step 5: public API field is `car`,
    not `carModel`. The lake's partition column name must not leak through.
    """
    _, client, _ = app_with_overrides(
        csv_response=_SAMPLE_CSV,
        driver_docs=[{"name": "Ludvík"}, {"name": "Alice"}, {"name": "Bob"}],
    )
    resp = client.get("/api/v1/leaderboard/best-laps")
    rows = resp.json()
    for row in rows:
        assert "car" in row, f"row {row} missing 'car' field"
        assert "carModel" not in row, f"row {row} leaks raw 'carModel' key"
    # Spot-check that a known car value round-tripped through the rename.
    bmw_rows = [r for r in rows if r["car"] == "bmw_1m"]
    assert bmw_rows, "bmw_1m not present after rename"


# ---------------------------------------------------------------------------
# Driver-name rewrite tests (spec §5.5 + §6.2)
# ---------------------------------------------------------------------------


def test_driver_name_is_rewritten_to_mongo_display_case(
    app_with_overrides: Callable,
) -> None:
    """Validates spec §5.5: when Mongo has a matching driver name, the response
    uses the Mongo display-case form (e.g. `Ludvík`, not raw lake `ludvik`).
    """
    _, client, _ = app_with_overrides(
        csv_response=_SAMPLE_CSV,
        driver_docs=[{"name": "Ludvík"}, {"name": "Alice"}, {"name": "Bob"}],
    )
    resp = client.get("/api/v1/leaderboard/best-laps")
    rows = resp.json()

    drivers = {r["driver"] for r in rows}
    assert "Ludvík" in drivers, f"Ludvík missing from {drivers}"
    assert "Alice" in drivers, f"Alice missing from {drivers}"
    assert "Bob" in drivers, f"Bob missing from {drivers}"
    # Confirm raw lowercase did NOT leak through for matched drivers.
    assert "ludvik" not in drivers
    assert "alice" not in drivers


def test_driver_without_mongo_match_keeps_raw_lowercase(
    app_with_overrides: Callable,
) -> None:
    """Validates spec §5.5 ("Fallback: if no Mongo match, keep the raw
    lowercase lake value — do not drop the row") and §R5.
    """
    # Only Ludvík is in Mongo. Alice and Bob should fall back to lowercase.
    _, client, _ = app_with_overrides(
        csv_response=_SAMPLE_CSV,
        driver_docs=[{"name": "Ludvík"}],
    )
    resp = client.get("/api/v1/leaderboard/best-laps")
    assert resp.status_code == 200
    rows = resp.json()

    drivers = {r["driver"] for r in rows}
    assert "Ludvík" in drivers, "matched driver should be display-case"
    # Fallback: unmatched lake values remain lowercase AND rows are NOT dropped.
    assert "alice" in drivers, "unmatched driver must fall back to lowercase"
    assert "bob" in drivers, "unmatched driver must fall back to lowercase"
    assert len(rows) == 3, "no row should be dropped just because Mongo has no match"


def test_empty_mongo_drivers_keeps_all_raw_lowercase(
    app_with_overrides: Callable,
) -> None:
    """Validates spec §5.5 fallback under the degenerate empty-Mongo case.
    When `drivers` is empty, every row must fall back to the raw lowercase
    lake value (nothing is dropped).
    """
    _, client, _ = app_with_overrides(
        csv_response=_SAMPLE_CSV,
        driver_docs=[],
    )
    resp = client.get("/api/v1/leaderboard/best-laps")
    assert resp.status_code == 200
    rows = resp.json()
    drivers = {r["driver"] for r in rows}
    assert drivers == {"ludvik", "alice", "bob"}
    assert len(rows) == 3


# ---------------------------------------------------------------------------
# Sort / rank expectations (spec §5.2: ORDER BY best_lap_ms ASC; §7.1 note)
# ---------------------------------------------------------------------------


def test_rows_sorted_by_best_lap_ms_ascending(app_with_overrides: Callable) -> None:
    """Validates spec §5.2: `ORDER BY track, carModel, experiment,
    best_lap_ms ASC`. Within a given (track, car, experiment) triple, rows
    must come back sorted by `best_lap_ms` ascending. The SQL does the
    sort — the API is expected to preserve it.

    Note: the frontend re-sorts after client-side filtering (spec §7.1),
    so the contract guarantees rows ARE ordered but the frontend does not
    rely on that. We assert it here because §5.2 promises it.
    """
    # Use a CSV whose rows are already in the expected server-side order,
    # then confirm the API preserves that order within a triple.
    csv = (
        "track,carModel,experiment,driver,best_lap_ms\r\n"
        "ks_nurburgring,bmw_1m,exp_42,ludvik,98342\r\n"
        "ks_nurburgring,bmw_1m,exp_42,alice,99108\r\n"
        "ks_nurburgring,bmw_1m,exp_42,bob,100300\r\n"
    )
    _, client, _ = app_with_overrides(
        csv_response=csv,
        driver_docs=[{"name": "Ludvík"}, {"name": "Alice"}, {"name": "Bob"}],
    )
    resp = client.get("/api/v1/leaderboard/best-laps")
    rows = resp.json()
    times = [r["best_lap_ms"] for r in rows]
    assert times == sorted(times), (
        f"best_lap_ms not monotonically non-decreasing: {times}"
    )


# ---------------------------------------------------------------------------
# Error-path tests (spec §6.2 + §7.4 + arch doc §3)
# ---------------------------------------------------------------------------


def test_missing_measurements_config_falls_back_to_hardcoded_url(
    app_with_overrides: Callable,
) -> None:
    """With no `measurements_deployment` configured (and no `measurements_url`
    env var), the endpoint now falls back to the module-level hardcoded URL
    defined in `leaderboard._FALLBACK_MEASUREMENTS_URL` rather than returning
    501. This is the intentional dev-mode behaviour — the value matches the
    `MEASUREMENTS_URL` defaultValue in `test-manager-backend/app.yaml`.
    """
    from api.routes.leaderboard import _FALLBACK_MEASUREMENTS_URL

    _, client, captured = app_with_overrides(measurements_url=None)
    resp = client.get("/api/v1/leaderboard/best-laps")
    assert resp.status_code == 200, f"expected 200, got {resp.status_code} body={resp.text}"
    assert captured["request_url"] == f"{_FALLBACK_MEASUREMENTS_URL}/api/query", (
        f"expected fallback URL, got {captured['request_url']!r}"
    )


def test_upstream_timeout_returns_504(app_with_overrides: Callable) -> None:
    """Validates spec §7.4 + §6.2 "504 on upstream timeout" and arch doc §3."""
    _, client, _ = app_with_overrides(raise_exc=httpx.TimeoutException("boom"))
    resp = client.get("/api/v1/leaderboard/best-laps")
    assert resp.status_code == 504, f"expected 504, got {resp.status_code} body={resp.text}"


def test_upstream_5xx_propagates_as_error(app_with_overrides: Callable) -> None:
    """Validates spec §6.2 + §7.4: upstream 5xx is surfaced (not silently
    swallowed). Arch doc §3 re-raises the upstream status via HTTPException.
    The spec §7.4 lists `500` as a fallback.

    The architecture doc implementation re-raises `response.status_code`
    directly when the upstream is non-2xx, so a 503 from the lake becomes
    a 503 to our caller. This test accepts any 5xx to cover both that
    pass-through behaviour and a bugfix that might map it to 500/502/504.
    """
    _, client, _ = app_with_overrides(
        response_status=503,
        csv_response="Upstream down",
    )
    resp = client.get("/api/v1/leaderboard/best-laps")
    assert 500 <= resp.status_code < 600, (
        f"upstream 503 must surface as 5xx, got {resp.status_code}"
    )


def test_generic_httpx_error_returns_500(app_with_overrides: Callable) -> None:
    """Validates spec §7.4: `500` on query or parse failure with the
    underlying error in `detail`.
    """
    _, client, _ = app_with_overrides(raise_exc=httpx.ConnectError("nope"))
    resp = client.get("/api/v1/leaderboard/best-laps")
    assert resp.status_code == 500
    assert "detail" in resp.json()


# ---------------------------------------------------------------------------
# Edge-case parser robustness
# ---------------------------------------------------------------------------


def test_malformed_best_lap_ms_row_is_skipped(app_with_overrides: Callable) -> None:
    """Validates arch doc §3 step 5 ("Drop malformed rows"): rows with a
    missing / non-numeric `best_lap_ms` must be dropped, not 500.
    """
    csv = (
        "track,carModel,experiment,driver,best_lap_ms\r\n"
        "t,c,e,valid,99000\r\n"
        "t,c,e,bad1,\r\n"
        "t,c,e,bad2,not_a_number\r\n"
    )
    _, client, _ = app_with_overrides(csv_response=csv)
    resp = client.get("/api/v1/leaderboard/best-laps")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["driver"] == "valid"
    assert rows[0]["best_lap_ms"] == 99000


def test_best_lap_ms_cast_to_int(app_with_overrides: Callable) -> None:
    """Validates arch doc §3 step 5: best_lap_ms is cast to int (lake CSV
    returns strings). A value like "98342.0" must come back as 98342 int.
    """
    csv = (
        "track,carModel,experiment,driver,best_lap_ms\r\n"
        "t,c,e,valid,98342.0\r\n"
    )
    _, client, _ = app_with_overrides(csv_response=csv)
    resp = client.get("/api/v1/leaderboard/best-laps")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["best_lap_ms"] == 98342
    assert isinstance(body[0]["best_lap_ms"], int)


def test_router_is_registered_under_api_v1(app_with_overrides: Callable) -> None:
    """Validates arch doc §4 File inventory + spec §6.1: the endpoint lives at
    `/api/v1/leaderboard/best-laps` (not e.g. `/leaderboard/best-laps` or
    `/best-laps`).

    The test-harness app mounts the same router with the same prefix as
    `app.py` (line 164), so a 404 here means the path shape in
    `routes/leaderboard.py` is wrong.
    """
    _, client, _ = app_with_overrides(csv_response="")
    # Wrong paths should 404, right path should 200.
    assert client.get("/leaderboard/best-laps").status_code == 404
    assert client.get("/best-laps").status_code == 404
    assert client.get("/api/v1/leaderboard/best-laps").status_code == 200
