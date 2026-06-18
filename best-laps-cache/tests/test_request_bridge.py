"""Unit tests for the req_id correlation + timeout bridge (no broker)."""

from __future__ import annotations

import threading
import time

from best_laps_cache.request_bridge import PendingRequests


def test_deliver_then_wait_returns_payload_and_deletes_slot():
    pending = PendingRequests()
    req_id = pending.open()
    assert pending.pending_count() == 1

    payload = {"_env": "rig", "track": {"car": {"drv": 90000}}}
    assert pending.deliver(req_id, payload) is True

    delivered, got = pending.wait(req_id, timeout=1.0)
    assert delivered is True
    assert got is payload
    # Slot removed after the waiter consumed it.
    assert pending.pending_count() == 0


def test_cross_thread_delivery():
    """SDF thread delivers slightly after the HTTP thread starts waiting."""
    pending = PendingRequests()
    req_id = pending.open()

    def deliver_later() -> None:
        time.sleep(0.05)
        pending.deliver(req_id, {"ok": 1})

    threading.Thread(target=deliver_later, daemon=True).start()
    delivered, got = pending.wait(req_id, timeout=2.0)
    assert delivered is True
    assert got == {"ok": 1}
    assert pending.pending_count() == 0


def test_timeout_path_cleans_up_slot():
    pending = PendingRequests()
    req_id = pending.open()
    delivered, got = pending.wait(req_id, timeout=0.05)
    assert delivered is False
    assert got is None
    # Timed-out slot must be removed so nothing lingers in RAM.
    assert pending.pending_count() == 0


def test_deliver_to_unknown_req_id_is_noop():
    pending = PendingRequests()
    # A late delivery (waiter already timed out + closed) must not raise.
    assert pending.deliver("does-not-exist", {"x": 1}) is False


def test_delivered_none_payload_is_distinguished_from_timeout():
    """Empty State (payload=None) is a *delivered* read, not a timeout."""
    pending = PendingRequests()
    req_id = pending.open()
    pending.deliver(req_id, None)
    delivered, got = pending.wait(req_id, timeout=1.0)
    assert delivered is True
    assert got is None


def test_close_is_idempotent():
    pending = PendingRequests()
    req_id = pending.open()
    pending.close(req_id)
    pending.close(req_id)
    assert pending.pending_count() == 0
