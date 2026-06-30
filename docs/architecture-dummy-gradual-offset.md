# Architecture — Dummy Replay Source: Linear Per-Lap Negative Offset

## What this is

An extension to `dummy-telemetry-source` (the captured-corpus replay Source
that feeds the best-laps cache / leaderboard with valid synthetic data). It adds
a linear, per-lap random NEGATIVE offset to the **live, in-progress** lap-time
fields of each replayed record, so a dashboard watching the live lap sees the
in-progress time pulled progressively further ahead (faster) as the lap
proceeds — from no change at the start/finish line down to `-A` (up to ~20 s
ahead) as the car reaches `normalizedCarPosition = 1`.

A single amplitude `A` is drawn once per lap and held constant; the applied
offset is `-A · normalizedCarPosition`, i.e. `0` at `pos=0` and `-A` at `pos=1`,
linear and monotonic. The feature touches ONLY the live fields (`iCurrentTime`,
`iDeltaLapTime`, `iEstimatedLapTime` and the sign/string mirrors that actually
track them in the corpus). It never perturbs `iBestTime`/`iLastTime`, which
remain the clean per-lap random bests minted by the pre-existing
`_apply_best_override`. The two mechanisms own disjoint field sets and keep
independent per-lap state, so the closed-lap leaderboard stays unpolluted by the
live drift.

## Why this design

- **Linear negative ramp.** The applied offset is the literal
  `offset = -A · clamp(pos, 0, 1)`: zero at `pos=0`, `-A` at `pos=1`, monotonic.
  Negative means it SUBTRACTS time from the live fields. It is a single
  arithmetic expression — no trig, no per-tick RNG.
  - **Tradeoff — lap-boundary step.** Because the shape is linear (not a
    zero-at-both-ends curve), the offset steps from `-A` back to `0` as
    `normalizedCarPosition` wraps 1→0 at the start/finish line. This
    discontinuity is INTENDED and accepted: it coincides exactly with the
    natural lap-clock reset, where `iCurrentTime` returns to ~0 for the new lap
    (and a fresh `A` is drawn). The live clock restarts at the same instant the
    offset does, so the step is not separately observable — the reset is the
    lap rollover itself, not a visible glitch.
- **Once-per-lap amplitude, deterministic within a lap.** Mirrors the existing
  `_apply_best_override` pattern exactly: a fresh `A = randint(0, MAX_LAP_OFFSET_MS)`
  is drawn on the first tick whose `completedLaps` differs from the last lap drawn
  for, then held constant for every subsequent tick of that lap. The only RNG call
  is the once-per-lap `randint` — no per-tick jitter — so within a lap the offset
  is a deterministic linear ramp, not noise.
- **Disjoint ownership of fields.** Best/last are the closed-lap min truth and are
  owned solely by `_apply_best_override`. The new `_apply_lap_offset` owns only the
  live fields. Both methods document their owned-field set in their docstrings, and
  they track separate lap-state pairs (`_current_lap`/`_current_best` vs
  `_offset_lap`/`_offset_amp`) so they cannot interfere even though both key on
  `completedLaps`.
- **String mirrors follow the corpus, not a guessed format (OQ-1).** See the data
  contract below — this was the gating investigation and it changed the
  implementation materially from the spec's tentative expectation.

## OQ-1 resolution — corpus string formats (load-bearing)

The spec (§7.4) *tentatively* expected `M:SS.mmm` strings re-derived from each int.
Inspecting the actual corpus (`data/replay_corpus.jsonl.gz`, all 17272 records)
proved otherwise, and the implementation follows the evidence:

| Field | int tracks? | Format / behaviour (confirmed across all 17272 records) |
|---|---|---|
| `currentTime` | **YES** | `"{m}:{s:02d}:{ms:03d}"` — minutes : zero-padded seconds : zero-padded ms. e.g. `1005→"0:01:005"`, `83210→"1:23:210"`, `176187→"2:56:187"`. **0 mismatches** vs. this formatter. Re-derived from the mutated `iCurrentTime`. |
| `deltaLapTime` | **NO** | Frozen literal `"-:--:---"` in **100%** of records, independent of `iDeltaLapTime` (even for large +/- ints). Left untouched; only the int is mutated. |
| `estimatedLapTime` | **NO** | Frozen literal `"35791:23:647"` in **100%** of records (= `INT_MAX` ms rendered), independent of `iEstimatedLapTime`. Left untouched; only the int is mutated. |
| `isDeltaPositive` | n/a | Stored as **int `0/1`** in the corpus, never a Python bool. Recomputed as `int(new_iDeltaLapTime >= 0)` to preserve value coherence AND type. |

Reproducing the corpus byte-for-byte therefore means: re-derive `currentTime` from
its int, but leave the two frozen string literals exactly as the corpus has them.
Note `normalizedCarPosition` is already within `[0,1]` throughout this corpus, but
the code still clamps (the source mirrors live AC, which can stray near the line).

## Data flow

```
records[] (corpus)
   └─ for each rec in run():               # dummy_source.py run()
        out = dict(rec)
        _apply_best_override(out)           # owns iBestTime / iLastTime  (unchanged)
        _apply_lap_offset(out)              # NEW — owns the live fields
            ├─ guard: MAX_LAP_OFFSET_MS<=0  → return (feature disabled)
            ├─ guard: normalizedCarPosition not numeric → return
            ├─ if completedLaps != _offset_lap:  _offset_amp = randint(0, MAX_LAP_OFFSET_MS)
            ├─ amp==0 → return
            ├─ pos = clamp(normalizedCarPosition, 0, 1)
            ├─ offset_ms = round(-amp * pos); offset_ms==0 → return   (negative: subtracts)
            ├─ iCurrentTime      = max(0, iCurrentTime + offset_ms) ; currentTime = _format_lap_time(iCurrentTime)
            ├─ iDeltaLapTime     += offset_ms (may go negative) ; isDeltaPositive = int(new >= 0)
            └─ iEstimatedLapTime = max(0, iEstimatedLapTime + offset_ms)   (estimatedLapTime literal untouched)
        stamp session_id / timestamp_ms
        serialize → produce → ac-telemetry-raw
```

Each int target is independently guarded against the `_INT_MAX` lap-1 sentinel
(and the lap-1 sentinel on `iEstimatedLapTime`), so sentinel records pass through
untouched on that field, mirroring `_apply_best_override`. Per-loop, `run()` calls
`_reset_lap_state()`, which now also clears `_offset_lap`/`_offset_amp` so every
replay loop re-randomizes (otherwise loop N's `completedLaps==1` would reuse loop
N−1's amplitude).

## File inventory

- **`dummy-telemetry-source/dummy_source.py`** (modified)
  - `__init__`: new `max_lap_offset_ms: int` param; new state fields
    `self._max_lap_offset_ms`, `self._offset_lap = None`, `self._offset_amp = None`.
  - `_reset_lap_state`: now also clears `_offset_lap`/`_offset_amp` (re-randomize per loop).
  - `_format_lap_time` (new `@staticmethod`): renders ms as the corpus
    `currentTime` format `"{m}:{s:02d}:{ms:03d}"`.
  - `_apply_lap_offset` (new method): draws/holds `A` per lap, computes
    `round(-A·clamp(pos))` (linear, negative), applies it to the live int fields
    (`iCurrentTime`/`iEstimatedLapTime` clamped to `max(0, …)`, `iDeltaLapTime`
    may go negative), re-derives `currentTime`, recomputes `isDeltaPositive` as
    int. Docstring states its owned-field set, the linear-negative shape, the
    lap-boundary-step tradeoff, and the OQ-1 frozen-string finding.
  - `run`: one new line, `self._apply_lap_offset(out)`, immediately after
    `self._apply_best_override(out)`.
- **`dummy-telemetry-source/main.py`** (modified)
  - Read `max_lap_offset_ms = int(os.environ.get("MAX_LAP_OFFSET_MS", "20000"))`
    alongside the existing tunables.
  - Pass `max_lap_offset_ms=max_lap_offset_ms` into the `DummyReplaySource(...)`
    constructor.

## Configuration

| Var | Default | Meaning |
|---|---|---|
| `MAX_LAP_OFFSET_MS` | `20000` | Upper bound (ms) for the per-lap amplitude `A`. `0` disables the feature (amplitude always 0 ⇒ offset always 0; records pass through untouched). |

## Integration with neighbouring features

- **`best-laps-lite` / leaderboard / best-laps cache** consume `ac-telemetry-raw`
  and fold on `iBestTime`/`iLastTime`. Because those fields are untouched here, the
  closed-lap leaderboard is unaffected by the live drift — the drift is visible only
  on live, in-progress lap-time displays (e.g. the live dashboard). No change to any
  other service, `quix.yaml`, or `app.yaml`.
- The offset is purely a producer-side mutation inside the replay Source; nothing
  downstream needs to be aware of it. Disabling it is a single env flip
  (`MAX_LAP_OFFSET_MS=0`).
```
