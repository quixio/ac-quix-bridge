/**
 * API client for the multi-driver live-positions leaderboard endpoint.
 *
 * @internal - Do not import directly. Use the `useLeaderboardApi()` hook instead.
 */

import { apiGet } from "./client"
import type { LivePositionEntry } from "@/types/leaderboard"

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
}
