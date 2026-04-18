"""Tests for /api/sessions.

The endpoint walks the QuixLake partition tree (via the native /partitions
endpoint, one S3 LIST per level) and returns a flat list of every unique
combination of partition columns. Frontend pre-loads this once on tab open
and does dropdown cascading client-side.

Tests stub `main._list_partition_children` with a dict keyed by the path
prefix, so each test can describe its own tree shape.
"""

from __future__ import annotations

import pytest

import main

PART_COLS = [
    "environment",
    "test_rig",
    "experiment",
    "driver",
    "track",
    "carModel",
    "session_id",
]


@pytest.fixture
def stub_partitions(monkeypatch: pytest.MonkeyPatch):
    class Recorder:
        def __init__(self) -> None:
            self.calls: list[str] = []
            self._responses: dict[str, list[str]] = {}

        def set(self, mapping: dict[str, list[str]]) -> None:
            self._responses = mapping

        async def __call__(self, path: str) -> list[str]:
            self.calls.append(path)
            return self._responses.get(path, [])

    recorder = Recorder()
    monkeypatch.setattr(main, "_list_partition_children", recorder)
    return recorder


def _full_path_to(values: dict[str, str]) -> str:
    """Build a Hive path from partition column values."""
    return "/".join(f"{c}={values[c]}" for c in PART_COLS if c in values)


def test_empty_tree_returns_empty_sessions(stub_partitions, client) -> None:
    stub_partitions.set({"": []})
    response = client.get("/api/sessions")
    assert response.status_code == 200
    assert response.json() == {"sessions": []}


def test_single_session_yields_one_row(stub_partitions, client) -> None:
    # A lake with one session per level, flat through all 7 partition cols.
    # Below session_id, three lap partitions (lap=1, lap=2, lap=3).
    ctx = {
        "environment": "prague_office",
        "test_rig": "g29",
        "experiment": "VideoStartSeek",
        "driver": "ludvik",
        "track": "ks_nurburgring",
        "carModel": "bmw_1m",
        "session_id": "2026-04-14T14:56:28.037Z",
    }
    tree: dict[str, list[str]] = {}
    for ci in range(len(PART_COLS)):
        prefix = _full_path_to({c: ctx[c] for c in PART_COLS[:ci]})
        col = PART_COLS[ci]
        tree[prefix] = [f"{col}={ctx[col]}"]
    session_path = _full_path_to(ctx)
    tree[session_path] = ["lap=1", "lap=2", "lap=3"]
    stub_partitions.set(tree)

    response = client.get("/api/sessions")
    assert response.status_code == 200
    body = response.json()
    assert body == {"sessions": [{**ctx, "laps": [1, 2, 3]}]}


def test_session_with_no_laps_has_empty_laps_list(stub_partitions, client) -> None:
    ctx = {c: "v" for c in PART_COLS}
    tree: dict[str, list[str]] = {}
    for ci in range(len(PART_COLS)):
        prefix = _full_path_to({c: ctx[c] for c in PART_COLS[:ci]})
        col = PART_COLS[ci]
        tree[prefix] = [f"{col}={ctx[col]}"]
    # session_path has no children — no lap=N entries
    tree[_full_path_to(ctx)] = []
    stub_partitions.set(tree)

    response = client.get("/api/sessions")
    assert response.status_code == 200
    assert response.json()["sessions"][0]["laps"] == []


def test_laps_sorted_numerically(stub_partitions, client) -> None:
    ctx = {c: "v" for c in PART_COLS}
    tree: dict[str, list[str]] = {}
    for ci in range(len(PART_COLS)):
        prefix = _full_path_to({c: ctx[c] for c in PART_COLS[:ci]})
        col = PART_COLS[ci]
        tree[prefix] = [f"{col}={ctx[col]}"]
    # Laps returned in non-sorted order; endpoint must sort numerically.
    tree[_full_path_to(ctx)] = ["lap=3", "lap=1", "lap=10", "lap=2"]
    stub_partitions.set(tree)

    response = client.get("/api/sessions")
    assert response.json()["sessions"][0]["laps"] == [1, 2, 3, 10]


def test_multiple_sessions_in_different_branches(stub_partitions, client) -> None:
    # Two environments, each with its own single-leaf path
    base = {
        "environment": None,  # varies
        "test_rig": "g29",
        "experiment": "X",
        "driver": "D",
        "track": "T",
        "carModel": "C",
        "session_id": "S",
    }
    envs = ["prague_office", "quix_office"]
    tree: dict[str, list[str]] = {"": [f"environment={e}" for e in envs]}
    for env in envs:
        for ci in range(1, len(PART_COLS)):
            prefix = f"environment={env}" + (
                "/" + "/".join(f"{c}={base[c]}" for c in PART_COLS[1:ci]) if ci > 1 else ""
            )
            col = PART_COLS[ci]
            tree[prefix] = [f"{col}={base[col]}"]
    stub_partitions.set(tree)

    response = client.get("/api/sessions")
    assert response.status_code == 200
    sessions = response.json()["sessions"]
    assert len(sessions) == 2
    assert {s["environment"] for s in sessions} == {"prague_office", "quix_office"}
    assert all(s["session_id"] == "S" for s in sessions)


def test_branching_at_experiment_level(stub_partitions, client) -> None:
    # One env, one rig, 2 experiments, each with one leaf path
    base_prefix = "environment=prague_office/test_rig=g29"
    tree: dict[str, list[str]] = {
        "": ["environment=prague_office"],
        "environment=prague_office": ["test_rig=g29"],
        base_prefix: ["experiment=A", "experiment=B"],
    }
    for exp in ["A", "B"]:
        p = f"{base_prefix}/experiment={exp}"
        for ci in range(3, len(PART_COLS)):
            col = PART_COLS[ci]
            tree[p] = [f"{col}=v"]
            p = f"{p}/{col}=v"
    stub_partitions.set(tree)

    response = client.get("/api/sessions")
    sessions = response.json()["sessions"]
    assert len(sessions) == 2
    assert {s["experiment"] for s in sessions} == {"A", "B"}


def test_tree_walk_visits_every_branch(stub_partitions, client) -> None:
    # Two envs, each fully populated to a single-leaf path. The walker must
    # visit every internal node: 1 root + 2 envs × 6 depths = 13 partitions calls.
    # (At depth 7 we have all columns and return without another call.)
    envs = ["e1", "e2"]
    tree: dict[str, list[str]] = {"": [f"environment={e}" for e in envs]}
    for env in envs:
        p = f"environment={env}"
        for ci in range(1, len(PART_COLS)):
            col = PART_COLS[ci]
            tree[p] = [f"{col}=v"]
            p = f"{p}/{col}=v"
    stub_partitions.set(tree)

    response = client.get("/api/sessions")
    assert response.status_code == 200
    assert len(response.json()["sessions"]) == 2
    # 1 root + 2 envs × 6 partition-level descents + 2 lap-lookup calls per leaf
    assert len(stub_partitions.calls) == 1 + 2 * 6 + 2


def test_filter_narrows_walk_to_matching_branch(stub_partitions, client) -> None:
    # Two envs under root; we request only env=e1 → walker must skip env=e2.
    base = {c: "v" for c in PART_COLS}
    tree: dict[str, list[str]] = {"": ["environment=e1", "environment=e2"]}
    # Populate both branches fully
    for env in ("e1", "e2"):
        p = f"environment={env}"
        for ci in range(1, len(PART_COLS)):
            col = PART_COLS[ci]
            tree[p] = [f"{col}={base[col]}"]
            p = f"{p}/{col}={base[col]}"
        tree[p] = ["lap=1"]  # one lap each
    stub_partitions.set(tree)

    response = client.get("/api/sessions?environment=e1")
    assert response.status_code == 200
    sessions = response.json()["sessions"]
    assert len(sessions) == 1
    assert sessions[0]["environment"] == "e1"
    # Walker must NOT have descended into environment=e2 at all
    assert not any(c.startswith("environment=e2") for c in stub_partitions.calls)


def test_multiple_filters_narrow_further(stub_partitions, client) -> None:
    # Two experiments under one env/rig; filter by both env and experiment.
    tree: dict[str, list[str]] = {
        "": ["environment=e1"],
        "environment=e1": ["test_rig=r1"],
        "environment=e1/test_rig=r1": ["experiment=a", "experiment=b"],
    }
    for exp in ("a", "b"):
        p = f"environment=e1/test_rig=r1/experiment={exp}"
        for ci in range(3, len(PART_COLS)):
            col = PART_COLS[ci]
            tree[p] = [f"{col}=v"]
            p = f"{p}/{col}=v"
        tree[p] = ["lap=1"]
    stub_partitions.set(tree)

    response = client.get("/api/sessions?environment=e1&experiment=b")
    sessions = response.json()["sessions"]
    assert len(sessions) == 1
    assert sessions[0]["experiment"] == "b"


def test_filter_for_missing_value_yields_empty(stub_partitions, client) -> None:
    tree: dict[str, list[str]] = {"": ["environment=e1"]}
    stub_partitions.set(tree)
    response = client.get("/api/sessions?environment=does_not_exist")
    assert response.status_code == 200
    assert response.json()["sessions"] == []


def test_partial_invalid_filter_yields_empty_without_error(stub_partitions, client) -> None:
    # User's URL has a valid environment but an invalid test_rig. The walk
    # should traverse into the valid env, then find nothing matching at the
    # rig level, and return an empty list — not crash.
    tree: dict[str, list[str]] = {
        "": ["environment=prague_office"],
        "environment=prague_office": ["test_rig=g29"],
    }
    stub_partitions.set(tree)
    response = client.get(
        "/api/sessions?environment=prague_office&test_rig=not_a_real_rig"
    )
    assert response.status_code == 200
    assert response.json()["sessions"] == []


def test_filter_value_with_special_chars_is_safe(stub_partitions, client) -> None:
    # Filter values are compared via exact string match against partition
    # names — no substring / path-traversal / injection concerns. A value
    # with slashes or equals signs just doesn't match anything.
    tree: dict[str, list[str]] = {"": ["environment=prague_office"]}
    stub_partitions.set(tree)
    response = client.get("/api/sessions?environment=../../etc/passwd")
    assert response.status_code == 200
    assert response.json()["sessions"] == []
    # Walker must NOT have attempted to descend with the bogus value
    assert not any("etc/passwd" in c for c in stub_partitions.calls)


def test_filtered_walk_makes_fewer_calls_than_full_walk(stub_partitions, client) -> None:
    # 3 envs, each fully populated to a leaf. Full walk visits every branch;
    # a filter to one env must visit only that branch.
    envs = ["e1", "e2", "e3"]
    tree: dict[str, list[str]] = {"": [f"environment={e}" for e in envs]}
    for env in envs:
        p = f"environment={env}"
        for ci in range(1, len(PART_COLS)):
            col = PART_COLS[ci]
            tree[p] = [f"{col}=v"]
            p = f"{p}/{col}=v"
        tree[p] = ["lap=1"]
    stub_partitions.set(tree)

    # Full walk: 1 root + 3 envs × (6 descents + 1 lap lookup) = 22 calls.
    response = client.get("/api/sessions")
    assert response.status_code == 200
    full_calls = len(stub_partitions.calls)
    assert full_calls == 1 + 3 * (6 + 1)

    # Reset and run the filtered walk.
    stub_partitions.calls.clear()
    response = client.get("/api/sessions?environment=e1")
    assert response.status_code == 200
    filtered_calls = len(stub_partitions.calls)
    # Filtered: 1 root + 1 env × (6 descents + 1 lap lookup) = 8 calls.
    assert filtered_calls == 1 + 1 * (6 + 1)
    assert filtered_calls < full_calls


def test_empty_filter_values_equivalent_to_no_filter(stub_partitions, client) -> None:
    # Explicitly empty-string params should NOT filter anything.
    tree: dict[str, list[str]] = {"": ["environment=e1", "environment=e2"]}
    for env in ("e1", "e2"):
        p = f"environment={env}"
        for ci in range(1, len(PART_COLS)):
            col = PART_COLS[ci]
            tree[p] = [f"{col}=v"]
            p = f"{p}/{col}=v"
        tree[p] = ["lap=1"]
    stub_partitions.set(tree)

    response = client.get(
        "/api/sessions?environment=&test_rig=&experiment=&driver=&track=&carModel=&session_id="
    )
    assert response.status_code == 200
    # Both environments still returned — empty-string param == not set
    envs_returned = {s["environment"] for s in response.json()["sessions"]}
    assert envs_returned == {"e1", "e2"}


def test_session_id_filter_narrows_to_one_session(stub_partitions, client) -> None:
    # Two sessions under the same full partition path, distinguished by session_id.
    base_prefix = "/".join(f"{c}=v" for c in PART_COLS[:-1])
    tree: dict[str, list[str]] = {"": ["environment=v"]}
    p = "environment=v"
    for ci in range(1, len(PART_COLS) - 1):
        col = PART_COLS[ci]
        tree[p] = [f"{col}=v"]
        p = f"{p}/{col}=v"
    tree[base_prefix] = ["session_id=s1", "session_id=s2"]
    for sid in ("s1", "s2"):
        tree[f"{base_prefix}/session_id={sid}"] = ["lap=1"]
    stub_partitions.set(tree)

    response = client.get("/api/sessions?session_id=s2")
    assert response.status_code == 200
    sessions = response.json()["sessions"]
    assert len(sessions) == 1
    assert sessions[0]["session_id"] == "s2"


def test_walker_propagates_errors(monkeypatch, client) -> None:
    async def boom(_path: str) -> list[str]:
        raise RuntimeError("lake down")

    monkeypatch.setattr(main, "_list_partition_children", boom)
    response = client.get("/api/sessions")
    assert response.status_code == 500
    assert "lake down" in response.json()["detail"]


@pytest.fixture
def _require_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure both required env vars look set so the env-var guard in
    _list_partition_children doesn't short-circuit before the transport
    mock gets hit. The URL/token values don't matter — the mock transport
    intercepts the outbound request regardless."""
    monkeypatch.setattr(main, "QUIXLAKE_URL", "https://test-lake.example.com")
    monkeypatch.setattr(main, "QUIX_LAKE_TOKEN", "test-token")


def test_quixlake_500_surfaces_as_502_with_upstream_status(
    monkeypatch, _require_env, client
) -> None:
    """When QuixLake itself returns 500, the proxy responds 502 (bad gateway)
    with the real upstream status in the detail — so the toast shows '500',
    not a generic error."""
    import httpx

    def mock_transport(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal error")

    monkeypatch.setattr(
        main,
        "_http_client",
        httpx.AsyncClient(transport=httpx.MockTransport(mock_transport)),
    )
    response = client.get("/api/sessions")
    assert response.status_code == 502
    assert "500" in response.json()["detail"]


def test_quixlake_403_surfaces_as_502_with_forbidden_detail(
    monkeypatch, _require_env, client
) -> None:
    import httpx

    def mock_transport(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="Access Forbidden")

    monkeypatch.setattr(
        main,
        "_http_client",
        httpx.AsyncClient(transport=httpx.MockTransport(mock_transport)),
    )
    response = client.get("/api/sessions")
    assert response.status_code == 502
    assert "403" in response.json()["detail"]
    assert "Forbidden" in response.json()["detail"]


def test_missing_quixlake_url_surfaces_clear_error(monkeypatch, client, caplog) -> None:
    """If QUIXLAKE_URL isn't configured, the error message must name the
    missing var (not leak a confusing httpx.InvalidURL stack trace)."""
    import logging

    monkeypatch.setattr(main, "QUIXLAKE_URL", None)
    with caplog.at_level(logging.ERROR, logger="main"):
        response = client.get("/api/sessions")
    assert response.status_code == 500
    assert "QUIXLAKE_URL" in response.json()["detail"]
    # Server logs should capture the misconfiguration (the full traceback
    # flows through caplog.text, which includes exc_info formatting).
    assert "QUIXLAKE_URL" in caplog.text


def test_missing_quix_lake_token_surfaces_clear_error(monkeypatch, client) -> None:
    monkeypatch.setattr(main, "QUIX_LAKE_TOKEN", None)
    response = client.get("/api/sessions")
    assert response.status_code == 500
    assert "QUIX_LAKE_TOKEN" in response.json()["detail"]


def test_both_env_vars_missing_names_both_in_error(monkeypatch, client) -> None:
    monkeypatch.setattr(main, "QUIXLAKE_URL", None)
    monkeypatch.setattr(main, "QUIX_LAKE_TOKEN", None)
    response = client.get("/api/sessions")
    assert response.status_code == 500
    detail = response.json()["detail"]
    assert "QUIXLAKE_URL" in detail
    assert "QUIX_LAKE_TOKEN" in detail


def test_quixlake_timeout_surfaces_as_504(monkeypatch, _require_env, client) -> None:
    """Timeouts to QuixLake map to 504 Gateway Timeout, not 500."""
    import httpx

    def mock_transport(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timed out", request=_request)

    monkeypatch.setattr(
        main,
        "_http_client",
        httpx.AsyncClient(transport=httpx.MockTransport(mock_transport)),
    )
    response = client.get("/api/sessions")
    assert response.status_code == 504
    assert "timed out" in response.json()["detail"].lower()
