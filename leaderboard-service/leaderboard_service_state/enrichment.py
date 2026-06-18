"""Session + DCM enrichment of raw ticks (ported from best-laps-cache).

``ac-telemetry-raw`` carries no ``track`` / ``carModel`` / ``driver`` /
``experiment`` / ``environment`` (``feedback_ac_raw_payload_fields``). Those live
in the session topic (track / carModel / playerName, once per session) and in DCM
(experiment / driver / environment, keyed by hostname). This module replicates the
proven ``leaderboard-service/api/live_telemetry.py`` enrichment so the SDF write
branch can form the full five-key group independently of the active-stream
consumer's module state (spec §8 — port a minimal Enrichment rather than couple
two consumers).

The three topics use unrelated key namespaces, so raw-tick enrichment takes the
most-recent entry of each cache (correct for the single-sim deployment). All DCM
HTTP happens on session-message / config-event arrival (rare), never per raw tick.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

import httpx

from .settings import Settings

logger = logging.getLogger(__name__)


class Enrichment:
    """Per-deployment session + experiment metadata caches."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._lock = threading.RLock()
        # hostname -> {track, carModel, playerName, updated_epoch}
        self._session_cache: dict[str, dict[str, Any]] = {}
        # hostname -> {experiment, driver, environment, fetched_epoch, updated_epoch}
        self._experiment_cache: dict[str, dict[str, Any]] = {}

    # -- session topic -----------------------------------------------------

    def handle_session_message(self, hostname: str, payload: dict[str, Any]) -> None:
        """Cache session metadata for *hostname* and force-refresh DCM."""
        track = str(payload.get("track") or "").strip()
        car = str(payload.get("carModel") or "").strip()
        player = str(payload.get("playerName") or "").strip()
        if not (track and car):
            return
        with self._lock:
            self._session_cache[hostname] = {
                "track": track,
                "carModel": car,
                "playerName": player,
                "updated_epoch": time.time(),
            }
        logger.info(
            "session cache updated: hostname=%s track=%s car=%s driver=%s",
            hostname,
            track,
            car,
            player,
        )
        self._refresh_experiment(hostname)

    # -- DCM config events -------------------------------------------------

    def handle_config_event(self, payload: dict[str, Any]) -> None:
        """React to a DCM ``ac-telemetry-config`` event (experiment/session)."""
        try:
            metadata = payload.get("metadata") or {}
            if not isinstance(metadata, dict):
                return
            category = metadata.get("category")
            event_type = metadata.get("type")
            target_key = str(metadata.get("target_key") or "").strip()
            event = str(payload.get("event") or "").strip().lower()
            if (
                category != "ac-telemetry"
                or event_type not in ("session", "experiment")
                or not target_key
            ):
                return
            if event == "deleted":
                with self._lock:
                    self._experiment_cache.pop(target_key, None)
                    if event_type == "session":
                        self._session_cache.pop(target_key, None)
                return
            self._refresh_experiment(target_key)
        except Exception:
            logger.exception("failed to apply DCM config event")

    # -- enrichment lookup (hot path) -------------------------------------

    def enrich(self, payload: dict[str, Any]) -> dict[str, str]:
        """Return ``{environment, experiment, track, carModel, driver}`` for a
        raw tick, preferring fields already on the payload, then the most-recent
        session + experiment caches. Unresolved fields come back as ``""``.
        """
        track = str(payload.get("track") or "").strip()
        car = str(payload.get("carModel") or payload.get("car") or "").strip()
        driver = str(payload.get("driver") or "").strip()
        experiment = str(payload.get("experiment") or "").strip()
        environment = str(payload.get("environment") or "").strip()

        with self._lock:
            session = self._latest(self._session_cache)
            exp = self._latest_with_driver(self._experiment_cache)
            exp_any = self._latest(self._experiment_cache)

        if not track and session:
            track = str(session[1].get("track") or "").strip()
        if not car and session:
            car = str(session[1].get("carModel") or "").strip()
        if not driver:
            if exp:
                driver = str(exp[1].get("driver") or "").strip()
            if not driver and session:
                driver = str(session[1].get("playerName") or "").strip()
        if not experiment and exp_any:
            experiment = str(exp_any[1].get("experiment") or "").strip()
        if not environment and exp_any:
            environment = str(exp_any[1].get("environment") or "").strip()

        return {
            "environment": environment,
            "experiment": experiment,
            "track": track,
            "carModel": car,
            "driver": driver,
        }

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _latest(
        cache: dict[str, dict[str, Any]],
    ) -> tuple[str, dict[str, Any]] | None:
        if not cache:
            return None
        return max(
            cache.items(),
            key=lambda kv: (
                float(kv[1].get("updated_epoch") or kv[1].get("fetched_epoch") or 0.0),
                kv[0],
            ),
        )

    @staticmethod
    def _latest_with_driver(
        cache: dict[str, dict[str, Any]],
    ) -> tuple[str, dict[str, Any]] | None:
        best: tuple[tuple[float, str], str, dict[str, Any]] | None = None
        for k, e in cache.items():
            if not str(e.get("driver") or "").strip():
                continue
            epoch = float(e.get("updated_epoch") or e.get("fetched_epoch") or 0.0)
            cand = ((epoch, k), k, e)
            if best is None or cand[0] > best[0]:
                best = cand
        return None if best is None else (best[1], best[2])

    def _refresh_experiment(self, hostname: str) -> None:
        config = self._fetch_from_dcm(hostname)
        now = time.time()
        with self._lock:
            self._experiment_cache[hostname] = {
                "experiment": str(config.get("experiment_id") or ""),
                "driver": str(config.get("driver") or ""),
                "environment": str(config.get("environment") or ""),
                "fetched_epoch": now,
                "updated_epoch": now,
            }

    def _fetch_from_dcm(self, hostname: str) -> dict[str, str]:
        empty = {"experiment_id": "", "driver": "", "environment": ""}
        base_url = self._settings.config_api_url
        if not base_url:
            return dict(empty)
        base = f"{base_url.rstrip('/')}/api/v1"
        headers: dict[str, str] = {}
        if self._settings.sdk_token:
            headers["Authorization"] = f"Bearer {self._settings.sdk_token}"
        try:
            with httpx.Client(timeout=self._settings.dcm_timeout_s) as client:
                resp = client.get(f"{base}/configurations", headers=headers)
                if resp.status_code != 200:
                    logger.warning(
                        "DCM list returned %d resolving experiment for %s",
                        resp.status_code,
                        hostname,
                    )
                    return dict(empty)
                data = resp.json()
                configs = (
                    data
                    if isinstance(data, list)
                    else data.get("data", data.get("items", []))
                )
                config_id: str | None = None
                for cfg in configs:
                    meta = cfg.get("metadata") or {}
                    if (
                        meta.get("type") == "experiment"
                        and meta.get("target_key") == hostname
                    ):
                        config_id = cfg.get("id") or cfg.get("_id")
                        break
                if not config_id:
                    logger.info("no experiment config in DCM for hostname=%s", hostname)
                    return dict(empty)
                content = self._fetch_latest_version_content(
                    client, base, config_id, headers
                )
                if content is None:
                    return dict(empty)
                return {
                    "experiment_id": str(content.get("experiment_id") or ""),
                    "driver": str(content.get("driver") or ""),
                    "environment": str(content.get("environment") or ""),
                }
        except Exception:
            logger.exception("DCM experiment lookup failed for hostname=%s", hostname)
            return dict(empty)

    @staticmethod
    def _fetch_latest_version_content(
        client: httpx.Client,
        base: str,
        config_id: str,
        headers: dict[str, str],
    ) -> dict[str, Any] | None:
        v_resp = client.get(
            f"{base}/configurations/{config_id}/versions", headers=headers
        )
        if v_resp.status_code != 200:
            return None
        versions = v_resp.json()
        if isinstance(versions, dict):
            versions = versions.get("data", versions.get("items", []))
        if not versions:
            return None
        latest = max(
            versions,
            key=lambda v: int(v.get("metadata", v).get("version", 0) or 0),
        )
        version = latest.get("metadata", latest).get("version")
        c_resp = client.get(
            f"{base}/configurations/{config_id}/versions/{version}/content",
            headers=headers,
        )
        if c_resp.status_code != 200:
            return None
        content = c_resp.json()
        return content if isinstance(content, dict) else None
