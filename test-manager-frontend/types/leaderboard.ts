/**
 * TypeScript types for the Leaderboard (best laps) feature.
 * Mirrors backend/api/models.py::BestLapEntry.
 */

export interface BestLapEntry {
  track: string
  car: string
  experiment: string
  driver: string
  best_lap_ms: number
  session_id: string | null
  lap_number: number | null
  achieved_at: string | null
}
