"""Unit tests for the Arrow-preferred / CSV-fallback lakehouse client.

All HTTP is mocked by monkeypatching ``_post_with_retry`` — no network.
"""

from __future__ import annotations

import io

import pyarrow as pa
import pytest

from best_laps_cache.lakehouse_client import (
    LakehouseClient,
    LakehouseQueryError,
    _LakeResponse,
)


def _arrow_stream_bytes(table: pa.Table) -> bytes:
    sink = io.BytesIO()
    with pa.ipc.new_stream(sink, table.schema) as writer:
        writer.write_table(table)
    return sink.getvalue()


def _patch_response(monkeypatch, resp: _LakeResponse) -> None:
    monkeypatch.setattr(
        LakehouseClient,
        "_post_with_retry",
        lambda self, url, sql: resp,
    )


def test_query_parses_arrow_stream(monkeypatch):
    table = pa.table(
        {
            "environment": ["dev"],
            "experiment": ["exp"],
            "track": ["spa"],
            "carModel": ["911"],
            "driver": ["Ada"],
            "iBestTime": [82345],
        }
    )
    _patch_response(
        monkeypatch,
        _LakeResponse(
            content_type="application/vnd.apache.arrow.stream",
            content=_arrow_stream_bytes(table),
            text="",
        ),
    )
    df = LakehouseClient("http://lake", "tok").query("SELECT 1")

    assert list(df["driver"]) == ["Ada"]
    assert list(df["iBestTime"]) == [82345]
    # Partition cols pinned to str even though Arrow could infer numerics.
    for col in ("environment", "experiment", "track", "carModel", "driver"):
        assert all(isinstance(v, str) for v in df[col])


def test_query_parses_csv_fallback(monkeypatch):
    csv = "environment,experiment,track,carModel,driver,iBestTime\n" "dev,exp,spa,911,Ada,82345\n"
    _patch_response(
        monkeypatch,
        _LakeResponse(content_type="text/csv", content=csv.encode(), text=csv),
    )
    df = LakehouseClient("http://lake", "tok").query("SELECT 1")

    assert list(df["driver"]) == ["Ada"]
    assert list(df["iBestTime"]) == [82345]
    # Digit-only carModel ("911") must stay a string, not coerce to int.
    assert df["carModel"].iloc[0] == "911"
    assert isinstance(df["carModel"].iloc[0], str)


def test_query_error_body_raises(monkeypatch):
    body = "\n# ERROR: Binder Error: no such column foo"
    _patch_response(
        monkeypatch,
        _LakeResponse(content_type="text/plain", content=body.encode(), text=body),
    )
    with pytest.raises(LakehouseQueryError):
        LakehouseClient("http://lake", "tok").query("SELECT foo")


def test_query_empty_text_returns_empty(monkeypatch):
    _patch_response(
        monkeypatch,
        _LakeResponse(content_type="text/csv", content=b"", text=""),
    )
    df = LakehouseClient("http://lake", "tok").query("SELECT 1")
    assert df.empty
