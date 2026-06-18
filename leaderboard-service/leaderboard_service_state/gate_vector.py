"""The single samples-to-gate-vector reducer (pure, no Kafka/RocksDB/IO).

``gate_vector_from_samples`` is the extracted interpolation core of
``leaderboard-service/api/routes/leaderboard_real.py`` ``_reduce_to_gate_vectors``
(the per-gate linear interpolation + monotonic clamp, ~lines 776-808). It is the
single source of truth for "lap position samples -> GATE_COUNT-length cumulative
gate vector", reused by:

* the live write-branch reducer (``pipeline.py``, new best laps from
  ``ac-telemetry-raw``);
* the boot-seed reducer (``seed.py``, cold-start lakehouse seed);
* the refactored ``_reduce_to_gate_vectors`` (no behaviour change — it now calls
  this helper for the inner loop).

Because the gate vector this produces is byte-for-byte what
``_HistoricalEntry.gate_vector`` already carries, the gate comparison algorithm
in ``api/gate_math.py`` consumes the State-stored value unchanged.
"""

from __future__ import annotations


def gate_vector_from_samples(
    samples: list[tuple[float, int]],
    gate_count: int,
) -> list[int]:
    """Reduce position samples to a ``gate_count``-length cumulative-ms vector.

    *samples* is a list of ``(normalizedCarPosition, iCurrentTime_ms)`` pairs for
    one lap, **sorted ascending by time** (the caller sorts; this matches the
    legacy ``samples = sorted(buckets[best_key], key=lambda x: x[1])`` step).
    ``iCurrentTime`` is AC's lap-relative clock (ms since lap start), so it is
    already the cumulative-time-at-position we want at each gate.

    For each gate ``i`` (boundary at position ``(i+1)/gate_count``) the time is
    linear-interpolated between the two bracketing samples; gates past the last
    sample fall back to the nearest sample's time. The result is clamped
    monotonic non-decreasing (a cumulative-time vector can never go backwards).
    Returns a list of ``gate_count`` ints (``0`` when *samples* is empty).
    """
    gate_vector: list[int] = [0] * gate_count
    if not samples:
        return gate_vector

    scan_from = 0
    n = len(samples)
    for i in range(gate_count):
        target = (i + 1) / gate_count
        interp_ts: float | None = None
        j = scan_from
        while j < n - 1:
            lo_pos, lo_ts = samples[j]
            hi_pos, hi_ts = samples[j + 1]
            if lo_pos <= target <= hi_pos:
                if hi_pos == lo_pos:
                    interp_ts = float(lo_ts)
                else:
                    frac = (target - lo_pos) / (hi_pos - lo_pos)
                    interp_ts = lo_ts + frac * (hi_ts - lo_ts)
                scan_from = j
                break
            j += 1
        if interp_ts is None:
            nearest = min(samples, key=lambda s, t=target: abs(s[0] - t))
            interp_ts = float(nearest[1])
        gate_vector[i] = max(0, int(interp_ts))

    for i in range(1, gate_count):
        if gate_vector[i] < gate_vector[i - 1]:
            gate_vector[i] = gate_vector[i - 1]

    return gate_vector
