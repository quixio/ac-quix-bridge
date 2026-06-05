from typing import Literal

from pydantic import BaseModel, Field

from .utils import now  # noqa: F401  (imported for consistency; now() used by routes)


# ---------------------------------------------------------------------------
# Leaderboard — Ghost Lap (single-driver live comparison vs personal best)
# ---------------------------------------------------------------------------


class LiveDriverState(BaseModel):
    """Snapshot of the currently-active driver's live lap state."""

    driver: str
    car: str
    track: str
    experiment: str
    current_lap: int
    current_lap_time_ms: int
    normalized_position: float
    best_lap_ms_session: int | None = None
    gate_times_ms: list[int | None] = Field(default_factory=lambda: [None] * 10)
    last_normalized_position: float = 0.0


class GhostSample(BaseModel):
    """One point on the ghost reference curve."""

    pos: float
    time_ms: int


class GhostReference(BaseModel):
    """The driver's personal-best lap on a (car, track), resampled to 101 fixed positions."""

    driver: str
    car: str
    track: str
    best_lap_ms: int
    samples: list[GhostSample]
    source_session_id: str
    source_lap: int
    segment_cumulative_ms: list[int] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Leaderboard — multi-driver live positions (Analysis Leaderboard tab)
# ---------------------------------------------------------------------------


class LivePositionEntry(BaseModel):
    """One row of the multi-driver live-positions table."""

    track: str
    car: str
    experiment: str
    driver: str
    best_lap_ms: int | None = None
    best_lap_number: int | None = None
    is_active: bool = False
    current_lap: int | None = None
    current_lap_time_ms: int
    rank: int
    last_gate_index: int | None = None
    last_gate_state: Literal["ahead", "behind", "neutral"] | None = None
    last_gate_delta_ms: int | None = None
    delta_at_last_gate_ms: int | None = None
