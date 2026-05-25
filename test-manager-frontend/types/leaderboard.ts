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
}
