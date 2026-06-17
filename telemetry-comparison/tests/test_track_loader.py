"""Tests for /api/track Mongo resolution (case-insensitive track matching).

The lake supplies `track` in its own casing (e.g. `Spa`) while Mongo
`track_layouts` keys are lowercase AC folder names (`spa`). `_resolve_mongo_doc`
must bridge that gap with an anchored case-insensitive `$regex` so the Mongo
doc is found instead of falling back to the bundled Nürburgring CSV.

These tests swap `mongo.get_mongo()` for a fake collection that interprets the
emitted `$regex`/`$options` filter exactly like Mongo would (anchored, `i`),
so we assert both the filter shape and the resolved provenance without a live
Mongo.
"""

from __future__ import annotations

import re

import pytest

import config
import mongo
import track_loader

# Canned lowercase docs mirroring the live `track_layouts` keys.
_SPA_DOC = {
    "_id": "spa/spa",
    "track": "spa",
    "layout": "spa",
    "length_m": 7004.0,
    "n_corners": 0,
    "corners": [],
    "points": [],
}
_DOCS = [_SPA_DOC]


def _matches(filt: dict, doc: dict) -> bool:
    """Apply a (subset of a) Mongo find filter to one doc, honouring the
    anchored case-insensitive `$regex` the loader emits."""
    for field, cond in filt.items():
        value = doc.get(field)
        if isinstance(cond, dict) and "$regex" in cond:
            flags = re.IGNORECASE if "i" in cond.get("$options", "") else 0
            if value is None or not re.search(cond["$regex"], value, flags):
                return False
        elif value != cond:
            return False
    return True


class _FakeCursor:
    def __init__(self, docs: list[dict]) -> None:
        self._docs = docs

    def sort(self, _spec: list[tuple[str, int]]) -> _FakeCursor:
        key = _spec[0][0]
        self._docs = sorted(self._docs, key=lambda d: d.get(key) or "")
        return self

    def limit(self, n: int) -> _FakeCursor:
        self._docs = self._docs[:n]
        return self

    def __iter__(self):
        return iter(self._docs)


class _FakeCollection:
    def __init__(self) -> None:
        self.last_filter: dict | None = None

    def find_one(self, filt: dict) -> dict | None:
        self.last_filter = filt
        return next((d for d in _DOCS if _matches(filt, d)), None)

    def find(self, filt: dict, _proj: dict | None = None) -> _FakeCursor:
        self.last_filter = filt
        return _FakeCursor([d for d in _DOCS if _matches(filt, d)])


class _FakeDB:
    def __init__(self, coll: _FakeCollection) -> None:
        self._coll = coll

    def __getitem__(self, _name: str) -> _FakeCollection:
        return self._coll


@pytest.fixture
def fake_coll(monkeypatch: pytest.MonkeyPatch) -> _FakeCollection:
    coll = _FakeCollection()
    monkeypatch.setattr(mongo, "get_mongo", lambda: _FakeDB(coll))
    return coll


def test_capitalized_track_resolves_lowercase_doc(fake_coll: _FakeCollection) -> None:
    """`track=Spa` (lake casing) must resolve the lowercase `spa` doc."""
    doc = track_loader._resolve_mongo_doc("Spa", "")
    assert doc is not None
    assert doc["_id"] == "spa/spa"
    # Filter is an anchored, case-insensitive regex (not an exact match).
    cond = fake_coll.last_filter["track"]
    assert cond["$options"] == "i"
    assert cond["$regex"] == "^Spa$"


def test_capitalized_id_resolves_lowercase_doc(fake_coll: _FakeCollection) -> None:
    """`track=Spa&layout=Spa` resolves `_id=spa/spa` case-insensitively."""
    doc = track_loader._resolve_mongo_doc("Spa", "Spa")
    assert doc is not None
    assert doc["_id"] == "spa/spa"
    assert fake_coll.last_filter["_id"]["$options"] == "i"
    assert fake_coll.last_filter["_id"]["$regex"] == "^Spa/Spa$"


def test_no_match_returns_none(fake_coll: _FakeCollection) -> None:
    assert track_loader._resolve_mongo_doc("Monaco", "") is None


def test_endpoint_returns_mongo_provenance(client, fake_coll: _FakeCollection) -> None:
    """GET /api/track?track=Spa returns track_file='mongo:spa/spa', not CSV."""
    resp = client.get("/api/track", params={"track": "Spa"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["track_file"] == "mongo:spa/spa"
    assert config.DEFAULT_TRACK_CSV not in body["track_file"]


def test_ci_exact_escapes_user_value() -> None:
    """The query-param value is re.escape-d (regex metachars are literal)."""
    cond = track_loader._ci_exact("ks_nurburgring.gp")
    assert cond["$regex"] == "^ks_nurburgring\\.gp$"
    assert cond["$options"] == "i"
