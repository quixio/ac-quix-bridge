"""Unit tests for the shared samples->gate-vector reducer (pure, no broker)."""

from __future__ import annotations

from leaderboard_service_state.gate_vector import gate_vector_from_samples


def test_empty_samples_returns_zeros():
    assert gate_vector_from_samples([], 10) == [0] * 10


def test_linear_lap_interpolates_proportionally():
    # A lap where time is linear in position: pos p -> 100_000 * p ms.
    samples = [(i / 100.0, int(100_000 * (i / 100.0))) for i in range(101)]
    vec = gate_vector_from_samples(samples, 10)
    assert len(vec) == 10
    # Gate i boundary is (i+1)/10; cumulative time ~= 100_000 * (i+1)/10.
    for i in range(10):
        expected = int(100_000 * (i + 1) / 10)
        assert abs(vec[i] - expected) <= 1, (i, vec[i], expected)
    # Last gate (lap line) is the full lap time.
    assert vec[-1] == 100_000


def test_monotonic_clamp_never_decreases():
    # Wobbly samples (position briefly goes backwards) must still yield a
    # non-decreasing cumulative vector.
    samples = [
        (0.0, 0),
        (0.30, 3000),
        (0.25, 3500),  # position wobble backwards, time forward
        (0.60, 6000),
        (1.0, 10000),
    ]
    vec = gate_vector_from_samples(samples, 5)
    assert all(vec[i] >= vec[i - 1] for i in range(1, 5))


def test_gate_count_length_respected():
    samples = [(p / 50.0, p * 200) for p in range(51)]
    assert len(gate_vector_from_samples(samples, 650)) == 650
    assert len(gate_vector_from_samples(samples, 3)) == 3


def test_nearest_fallback_when_no_bracketing_pair():
    # Samples never reach high positions; high gates fall back to the nearest
    # (last) sample's time, clamped monotonic.
    samples = [(0.0, 0), (0.2, 2000), (0.4, 4000)]
    vec = gate_vector_from_samples(samples, 10)
    assert vec[-1] == 4000  # nearest to 1.0 is the 0.4/4000 sample
    assert all(vec[i] >= vec[i - 1] for i in range(1, 10))
