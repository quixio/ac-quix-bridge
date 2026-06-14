"""Driver identity tests: required+validated email, company, and the folded
`name_key` that enforces driver uniqueness (and is the Test.driver -> Driver
join key for downstream auto-email).

Name is the lake identity, so it is locked after create. Email/company are
required on create and updatable; name is not updatable.
"""

import logging
from typing import Any

import pytest
from fastapi.testclient import TestClient

from api.mongo import (
    backfill_driver_name_keys,
    ensure_driver_indexes,
    get_mongo,
)


def _payload(
    name: str, email: str = "x@example.com", company: str = "Acme"
) -> dict[str, Any]:
    return {"name": name, "email": email, "company": company}


# ---------------------------------------------------------------------------
# Create: required + validated fields
# ---------------------------------------------------------------------------


def test_create_requires_email(client: TestClient) -> None:
    response = client.post(
        "/api/v1/drivers", json={"name": "Daniel", "company": "Quix"}
    )
    assert response.status_code == 422


def test_create_requires_company(client: TestClient) -> None:
    response = client.post(
        "/api/v1/drivers", json={"name": "Daniel", "email": "daniel@quix.io"}
    )
    assert response.status_code == 422


def test_create_rejects_invalid_email(client: TestClient) -> None:
    response = client.post(
        "/api/v1/drivers", json=_payload("Daniel", email="not-an-email")
    )
    assert response.status_code == 422


def test_create_rejects_overlong_company(client: TestClient) -> None:
    response = client.post(
        "/api/v1/drivers", json=_payload("Daniel", company="C" * 201)
    )
    assert response.status_code == 422


def test_create_stores_email_lowercased_and_company(client: TestClient) -> None:
    response = client.post(
        "/api/v1/drivers",
        json=_payload("Daniel Lastic", email="Daniel@QUIX.io", company="Quix"),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["email"] == "daniel@quix.io"
    assert data["company"] == "Quix"


# ---------------------------------------------------------------------------
# Create: name uniqueness via folded name_key (protects the lake)
# ---------------------------------------------------------------------------


def test_duplicate_name_exact_conflicts(client: TestClient) -> None:
    assert (
        client.post("/api/v1/drivers", json=_payload("Alice", "a@x.io")).status_code
        == 200
    )
    response = client.post("/api/v1/drivers", json=_payload("Alice", "a2@x.io"))
    assert response.status_code == 409


def test_duplicate_name_case_insensitive_conflicts(client: TestClient) -> None:
    assert (
        client.post("/api/v1/drivers", json=_payload("Alice", "a@x.io")).status_code
        == 200
    )
    response = client.post("/api/v1/drivers", json=_payload("alice", "a2@x.io"))
    assert response.status_code == 409


def test_duplicate_name_accent_insensitive_conflicts(client: TestClient) -> None:
    assert (
        client.post("/api/v1/drivers", json=_payload("Petr Čech", "p@x.io")).status_code
        == 200
    )
    response = client.post("/api/v1/drivers", json=_payload("Petr Cech", "p2@x.io"))
    assert response.status_code == 409


def test_duplicate_name_whitespace_insensitive_conflicts(client: TestClient) -> None:
    assert (
        client.post("/api/v1/drivers", json=_payload("Petr Cech", "p@x.io")).status_code
        == 200
    )
    response = client.post("/api/v1/drivers", json=_payload("Petr   Cech", "p2@x.io"))
    assert response.status_code == 409


# ---------------------------------------------------------------------------
# Create: email uniqueness
# ---------------------------------------------------------------------------


def test_duplicate_email_conflicts(client: TestClient) -> None:
    assert (
        client.post("/api/v1/drivers", json=_payload("Alice", "dup@x.io")).status_code
        == 200
    )
    response = client.post("/api/v1/drivers", json=_payload("Bob", "dup@x.io"))
    assert response.status_code == 409


def test_duplicate_email_case_insensitive_conflicts(client: TestClient) -> None:
    assert (
        client.post("/api/v1/drivers", json=_payload("Alice", "Dup@x.io")).status_code
        == 200
    )
    response = client.post("/api/v1/drivers", json=_payload("Bob", "dup@x.io"))
    assert response.status_code == 409


# ---------------------------------------------------------------------------
# Update: email/company editable, name locked
# ---------------------------------------------------------------------------


def test_update_email_and_company(client: TestClient) -> None:
    created = client.post(
        "/api/v1/drivers", json=_payload("Daniel", "old@x.io", "OldCo")
    ).json()
    driver_id = created["driver_id"]

    response = client.put(
        f"/api/v1/drivers/{driver_id}",
        json={"email": "New@x.io", "company": "NewCo"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["email"] == "new@x.io"
    assert data["company"] == "NewCo"
    assert data["name"] == "Daniel"


def test_update_name_is_ignored(client: TestClient) -> None:
    created = client.post("/api/v1/drivers", json=_payload("Daniel", "d@x.io")).json()
    driver_id = created["driver_id"]

    # name is not an updatable field -> no updatable fields supplied -> 400
    response = client.put(f"/api/v1/drivers/{driver_id}", json={"name": "Renamed"})
    assert response.status_code == 400

    # name unchanged
    assert client.get(f"/api/v1/drivers/{driver_id}").json()["name"] == "Daniel"


def test_update_rejects_invalid_email(client: TestClient) -> None:
    created = client.post("/api/v1/drivers", json=_payload("Daniel", "d@x.io")).json()
    response = client.put(
        f"/api/v1/drivers/{created['driver_id']}", json={"email": "nope"}
    )
    assert response.status_code == 422


def test_update_duplicate_email_conflicts(client: TestClient) -> None:
    client.post("/api/v1/drivers", json=_payload("Alice", "alice@x.io"))
    bob = client.post("/api/v1/drivers", json=_payload("Bob", "bob@x.io")).json()

    response = client.put(
        f"/api/v1/drivers/{bob['driver_id']}", json={"email": "alice@x.io"}
    )
    assert response.status_code == 409


# ---------------------------------------------------------------------------
# Boot-time backfill + soft-fail index build (mongo.py)
# ---------------------------------------------------------------------------


def test_backfill_sets_name_key_on_legacy_docs(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    db = get_mongo()
    db.drivers.insert_one({"_id": "DRV-9001", "name": "Petr Čech"})

    with caplog.at_level(logging.INFO, logger="api.mongo"):
        updated = backfill_driver_name_keys(db)

    assert updated == 1
    doc = db.drivers.find_one({"_id": "DRV-9001"})
    assert doc is not None
    assert doc["name_key"] == "petr cech"
    # the backfill logs which driver got keyed (verifiable via `quix logs`)
    assert "DRV-9001" in caplog.text
    assert "petr cech" in caplog.text


def test_backfill_is_idempotent(client: TestClient) -> None:
    db = get_mongo()
    db.drivers.insert_one({"_id": "DRV-9001", "name": "Daniel"})

    assert backfill_driver_name_keys(db) == 1
    assert backfill_driver_name_keys(db) == 0


def test_backfill_leaves_existing_name_key_untouched(client: TestClient) -> None:
    db = get_mongo()
    # One doc already keyed (must NOT be recomputed/overwritten), one missing it.
    db.drivers.insert_one({"_id": "DRV-9001", "name": "Has Key", "name_key": "preset"})
    db.drivers.insert_one({"_id": "DRV-9002", "name": "Needs Key"})

    assert backfill_driver_name_keys(db) == 1  # only the doc missing a key
    assert db.drivers.find_one({"_id": "DRV-9001"})["name_key"] == "preset"
    assert db.drivers.find_one({"_id": "DRV-9002"})["name_key"] == "needs key"


def test_backfill_skips_docs_without_a_name(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    db = get_mongo()
    db.drivers.insert_one({"_id": "DRV-9001"})  # no name, no name_key

    with caplog.at_level(logging.WARNING, logger="api.mongo"):
        assert backfill_driver_name_keys(db) == 0
    assert "name_key" not in db.drivers.find_one({"_id": "DRV-9001"})
    assert "DRV-9001" in caplog.text  # the skip is surfaced as a warning


def test_ensure_indexes_degrades_on_duplicate_name_key(client: TestClient) -> None:
    db = get_mongo()
    # Drop the unique index built at boot so we can seed colliding data, then
    # rebuild — this is the real "boot finds pre-existing duplicates" scenario.
    db.drivers.drop_indexes()
    db.drivers.insert_one({"_id": "DRV-9001", "name": "Daniel", "name_key": "daniel"})
    db.drivers.insert_one({"_id": "DRV-9002", "name": "daniel", "name_key": "daniel"})

    # Must not raise — degrades to app-level enforcement instead of crashing.
    ensure_driver_indexes(db)
