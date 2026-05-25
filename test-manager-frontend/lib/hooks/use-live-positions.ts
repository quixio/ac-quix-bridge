"use client"

/**
 * Multi-driver live-positions hook.
 *
 * Polls `/leaderboard/live-positions` every 3.5 s. Re-derives distinct
 * `tracks`, `cars`, `experiments` lists from the latest response so the
 * filter dropdowns can populate themselves regardless of which groups
 * the backend ships.
 *
 * The active driver's `current_lap_time_ms` advances on every poll;
 * historical rows are mostly stable but their ghost estimates rotate
 * around the active driver's current map position.
 */

import { useEffect, useMemo, useState } from "react"
import { useLeaderboardApi } from "./use-api"
import type { LivePositionEntry } from "@/types/leaderboard"

const POLL_INTERVAL_MS = 8000

export interface UseLivePositionsResult {
  rows: LivePositionEntry[]
  tracks: string[]
  cars: string[]
  experiments: string[]
  loading: boolean
  error: Error | null
}

function distinctSorted(values: string[]): string[] {
  return Array.from(new Set(values)).sort((a, b) => a.localeCompare(b))
}

export function useLivePositions(): UseLivePositionsResult {
  const api = useLeaderboardApi()
  const [rows, setRows] = useState<LivePositionEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<Error | null>(null)

  // `api` reference changes when the auth token rotates — re-fire the
  // closure so the new token is used immediately.
  useEffect(() => {
    let cancelled = false

    async function tick() {
      try {
        const next = await api.getLivePositions()
        if (cancelled) return
        setRows(next)
        setError(null)
        setLoading(false)
      } catch (err) {
        if (cancelled) return
        setError(
          err instanceof Error ? err : new Error("live-positions fetch failed"),
        )
        setLoading(false)
      }
    }

    tick()
    const id = setInterval(tick, POLL_INTERVAL_MS)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [api])

  const tracks = useMemo(() => distinctSorted(rows.map((r) => r.track)), [rows])
  const cars = useMemo(() => distinctSorted(rows.map((r) => r.car)), [rows])
  const experiments = useMemo(
    () => distinctSorted(rows.map((r) => r.experiment)),
    [rows],
  )

  return { rows, tracks, cars, experiments, loading, error }
}
