/**
 * TypeScript types for the multi-driver live-positions leaderboard.
 * Mirrors backend/api/models.py: `LivePositionEntry`.
 */

export interface LivePositionEntry {
  track: string
  car: string
  experiment: string
  driver: string
  /** Historical best lap on this (track, car, experiment, driver). `null`
   * for the active driver until he completes his first lap. */
  best_lap_ms: number | null
  /** 1-indexed lap number on which `best_lap_ms` was set. `null` when
   * `best_lap_ms` is `null` (active driver before his first complete lap). */
  best_lap_number: number | null
  /** Exactly one entry per (track, car, experiment) group has this `true`. */
  is_active: boolean
  /** 1-indexed lap. Populated only for the active driver. */
  current_lap: number | null
  /** Active driver: real elapsed time on the current lap. Historical
   * drivers: ghost-estimated time at the active driver's current map
   * position. */
  current_lap_time_ms: number
  /** 1..5 within the (track, car, experiment) group. Server-computed
   * from cumulative-at-sector-boundary times. */
  rank: number
  /** 0..9; the latest checkpoint gate the active driver has crossed on
   * the current lap. Echoed onto every row in the same group so the
   * frontend can render per-historical deltas against a consistent
   * label. `null` before gate 1 of every lap. */
  last_gate_index?: number | null
  /** Colour state for the active row's "At Position" column. Computed
   * server-side from the active driver's gate-crossing time vs. the
   * **median** of every cached historical's same-gate time, with a
   * 50 ms neutral band: "ahead" => active is >50 ms faster than the
   * median; "behind" => >50 ms slower; "neutral" => inside the band or
   * no historicals available. `null` on non-active rows and before
   * the active driver crosses gate 1 of his current lap. */
  last_gate_state?: "ahead" | "behind" | "neutral" | null
  /** Active row only. `active.gate_times_ms[i*] -
   * median(historicals.gate_vector[i*])`. Positive means the active is
   * slower than the median historical; negative means faster. `null`
   * before gate 1 or when no historicals are available. */
  last_gate_delta_ms?: number | null
  /** Per-historical inline delta (spec §7.2). Set only on historical
   * rows: `active.gate_times_ms[i*] - this_historical.gate_vector[i*]`.
   * Positive => active is slower than the historical at that gate.
   * `null` on the active row, on non-active rows when no active driver,
   * or before gate 1 of the active driver's current lap. */
  delta_at_last_gate_ms?: number | null
  /** Cumulative time at the LAST crossed gate (i*). For historicals it's
   * `gate_vector[i*]`; for the active row it's `gate_times_ms[i*]`.
   * Sticky between crossings. The dual gap chips in
   * live-positions-table read this directly so a rank-shuffled
   * neighbour mid-lap carries its own correct reference instead of
   * falling back to the N+1 `current_lap_time_ms` value. */
  gate_time_at_crossing_ms?: number | null
}
