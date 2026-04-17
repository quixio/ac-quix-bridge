/**
 * API client for Leaderboard
 *
 * @internal - Do not import directly. Use the `useLeaderboardApi()` hook instead.
 */

import { apiGet } from "./client"
import type { BestLapEntry } from "@/types/leaderboard"

export const leaderboardApi = {
  getBestLaps: (
    token?: string | null,
    refreshToken?: () => Promise<string | null>
  ) => {
    return apiGet<BestLapEntry[]>("/leaderboard/best-laps", undefined, token, refreshToken)
  },
}
