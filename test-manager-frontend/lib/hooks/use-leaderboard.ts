"use client"

import { useEffect, useMemo, useState } from "react"
import { useLeaderboardApi } from "./use-api"
import type { BestLapEntry } from "@/types/leaderboard"

/**
 * Fetch the full per-(track, car, experiment, driver) best-lap matrix once
 * on mount. Derived `tracks`, `cars`, `experiments` are alphabetically
 * sorted distinct values from the payload — consumers feed these into
 * dropdowns and filter `data` in memory.
 *
 * The hook intentionally does NOT refetch on filter change. That's the
 * whole point of the one-shot-fetch + client-side-filter design (see
 * `dev-planning/leaderboard-best-laps.md` §5.6).
 */
export function useLeaderboard() {
  const leaderboardApi = useLeaderboardApi()
  const [data, setData] = useState<BestLapEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<Error | null>(null)
  const [refetchTrigger, setRefetchTrigger] = useState(0)

  useEffect(() => {
    let cancelled = false

    async function fetchBestLaps() {
      try {
        setLoading(true)
        setError(null)
        const rows = await leaderboardApi.getBestLaps()
        if (!cancelled) setData(rows ?? [])
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err : new Error("Failed to fetch leaderboard"))
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    fetchBestLaps()
    return () => {
      cancelled = true
    }
  }, [refetchTrigger, leaderboardApi])

  // Derive filter option lists from the payload. Sorted alphabetically so
  // the caller can `[0]` to get the first alphabetical value for default
  // selection without extra work.
  const tracks = useMemo(
    () => Array.from(new Set(data.map((r) => r.track))).sort((a, b) => a.localeCompare(b)),
    [data]
  )
  const cars = useMemo(
    () => Array.from(new Set(data.map((r) => r.car))).sort((a, b) => a.localeCompare(b)),
    [data]
  )
  const experiments = useMemo(
    () => Array.from(new Set(data.map((r) => r.experiment))).sort((a, b) => a.localeCompare(b)),
    [data]
  )

  const refetch = () => setRefetchTrigger((prev) => prev + 1)

  return { data, loading, error, refetch, tracks, cars, experiments }
}
