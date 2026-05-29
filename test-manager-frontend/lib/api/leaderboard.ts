/**
 * API client for the multi-driver leaderboard endpoints.
 *
 * @internal - Do not import directly. Use the `useLeaderboardApi()` hook instead.
 *
 * Endpoints:
 *   - `/leaderboard/live-positions` — full leaderboard payload (sim + real),
 *     consumed by Live Sector Comparison via the WebSocket stream.
 *   - `/leaderboard/experiment-tree` — single round-trip nested dict
 *     `{experiment: {track: [car, ...]}}` driving the cascading
 *     Experiment / Track / Car dropdowns.
 *   - `/leaderboard/best-laps` — per-driver best lap for one
 *     (experiment, track, car).
 *
 * Both dropdown- and best-laps endpoints are called on user navigation
 * only (open tab, pick experiment/track/car). They go through the same
 * `apiGet` retry/refresh path as the rest of the app.
 */

import { apiGet } from "./client"
import type { LivePositionEntry } from "@/types/leaderboard"

/**
 * Nested tree returned by `/leaderboard/experiment-tree`.
 *
 * Shape: `{experiment: {track: [car, ...]}}`. All keys + the leaf
 * `string[]` are sorted lexicographically server-side, but consumers
 * should sort defensively before render.
 */
export type ExperimentTree = Record<string, Record<string, string[]>>

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
   * Fetch the full `{experiment: {track: [car, ...]}}` tree in one
   * round-trip. Backend builds this from a single lake query with
   * `experiment IN (...)` partition pruning; cheap enough to refetch
   * on tab mount without a client cache.
   */
  getExperimentTree: async (
    token?: string | null,
    refreshToken?: () => Promise<string | null>,
  ): Promise<ExperimentTree> => {
    return apiGet<ExperimentTree>(
      "/leaderboard/experiment-tree",
      undefined,
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
