"""State-native (RocksDB) re-platform of the leaderboard historical path.

This package mirrors ``best-laps-cache/best_laps_cache/`` but carries the
richer per-driver value the leaderboard needs: each best lap stores its full
``GATE_COUNT``-length gate vector so the gate comparison algorithm
(``api/gate_math.py``) runs entirely off State with zero lake queries on the
request path.

Durable store: QuixStreams native State (RocksDB) on the ``state:`` volume.
No SQL / SQLite / other DB anywhere. The lakehouse is queried only to seed an
empty State (cold start, marker-gated); all new best laps are reconstructed
live from ``ac-telemetry-raw``.
"""
