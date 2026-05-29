/**
 * API client for the multi-driver leaderboard endpoints.
 *
 * @internal - Do not import directly. Use the `useLeaderboardApi()` hook instead.
 *
 * Endpoints:
 *   - `/leaderboard/live-positions` — full leaderboard payload (sim + real),
 *     consumed by Live Sector Comparison via the WebSocket stream.
 *   - `/leaderboard/experiments` — distinct experiments in the lake.
 *   - `/leaderboard/experiment-options` — distinct (tracks, cars) for one
 *     experiment.
 *   - `/leaderboard/best-laps` — per-driver best lap for one
 *     (experiment, track, car).
 *
 * The three dropdown endpoints are called on user navigation only (open
 * tab, pick experiment, pick track/car). They go through the same
 * `apiGet` retry/refresh path as the rest of the app.
 */

import { apiGet } from "./client"
import type { LivePositionEntry } from "@/types/leaderboard"

export interface ExperimentOptions {
  tracks: string[]
  cars: string[]
}

export interface BestLapRow {
  driver: string
  best_lap_ms: number
}

export const leaderboardApi = {
  /**
   * Fetch the full leaderboard. Returns 60 rows in LOCAL_DEV_MODE
   * (3 tracks × 2 cars × 2 experiments × 5 drivers). The caller is
   * responsible for filtering by (track, car, experiment) and sorting
   * by `rank`.
   */
  getLivePositions: async (
    token?: string | null,
    refreshToken?: () => Promise<string | null>,
  ): Promise<LivePositionEntry[]> => {
    return apiGet<LivePositionEntry[]>(
      "/leaderboard/live-positions",
      undefined,
      token,
      refreshToken,
    )
  },

  /**
   * Fetch all distinct experiments available in the lake. Sorted
   * ascending. Empty array when the lake is empty.
   */
  getExperiments: async (
    token?: string | null,
    refreshToken?: () => Promise<string | null>,
  ): Promise<string[]> => {
    return apiGet<string[]>(
      "/leaderboard/experiments",
      undefined,
      token,
      refreshToken,
    )
  },

  /**
   * Fetch the (tracks, cars) options available for one experiment.
   * Both lists are sorted ascending and contain only non-empty values.
   */
  getExperimentOptions: async (
    experiment: string,
    token?: string | null,
    refreshToken?: () => Promise<string | null>,
  ): Promise<ExperimentOptions> => {
    return apiGet<ExperimentOptions>(
      "/leaderboard/experiment-options",
      { experiment },
      token,
      refreshToken,
    )
  },

  /**
   * Fetch the per-driver best laps for one (experiment, track, car).
   * Sorted ascending by `best_lap_ms`; driver names are mapped to the
   * Mongo display case server-side.
   */
  getBestLaps: async (
    experiment: string,
    track: string,
    car: string,
    token?: string | null,
    refreshToken?: () => Promise<string | null>,
  ): Promise<BestLapRow[]> => {
    return apiGet<BestLapRow[]>(
      "/leaderboard/best-laps",
      { experiment, track, car },
      token,
      refreshToken,
    )
  },
}
