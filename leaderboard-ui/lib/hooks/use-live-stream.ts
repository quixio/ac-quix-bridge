"use client"

/**
 * Leaderboard live-stream hook.
 * Identical to the test-manager-frontend version except:
 *   1. `buildStreamUrl` derives the WS host from window.location (UI and API
 *      share one origin — static export served by leaderboard-service).
 *      In development (`next dev`), NEXT_PUBLIC_LEADERBOARD_SERVICE_URL may
 *      override the host to point at a locally running FastAPI.
 *   2. The gate-crossing HTTP refetch uses getApiUrl() (same-origin in prod).
 */

import { useEffect, useMemo, useRef, useState } from "react"

import { getApiUrl } from "@/lib/api/client"
import { useQuixAuth } from "@/lib/contexts/quix-auth-context"
import type { LivePositionEntry } from "@/types/leaderboard"

export interface LiveCombo {
  experiment: string
  track: string
  car: string
}

export interface FreezeEvent {
  crossedAtGate: number
  activeAtCrossingMs: number
  historicalAtCrossing: Record<string, number>
  activeAtCrossingGateMs: number | null
  stamp: number
}

export interface UseLiveStreamResult {
  rows: LivePositionEntry[]
  tracks: string[]
  cars: string[]
  experiments: string[]
  loading: boolean
  error: Error | null
  isLive: boolean
  liveCombo: LiveCombo | null
  freezeEvent: FreezeEvent | null
}

interface SnapshotMessage {
  type: "snapshot"
  rows: LivePositionEntry[]
}

interface ActiveMutation {
  driver: string
  track: string
  car: string
  experiment: string
  current_lap: number | null
  current_lap_time_ms: number
  normalized_position: number
  last_gate_index: number | null
  last_gate_state: "ahead" | "behind" | "neutral" | null
  last_gate_delta_ms: number | null
  active_at_crossing_ms?: number | null
}

interface ActiveMessage {
  type: "active"
  row: ActiveMutation
  historical_deltas?: Record<string, number>
  historical_at_positions_next?: Record<string, number>
  historical_at_positions_at_crossing?: Record<string, number>
}

interface PingMessage {
  type: "ping"
}

interface ActiveStateMessage {
  type: "active_state"
  is_active: boolean
  driver: string | null
  track: string | null
  car: string | null
  experiment: string | null
  environment: string | null
}

type StreamMessage =
  | SnapshotMessage
  | ActiveMessage
  | PingMessage
  | ActiveStateMessage

const RECONNECT_BACKOFF_MS = [1000, 2000, 4000, 10000]

function buildStreamUrl(token: string | null): string {
  // Production: UI and API share one origin, so derive the WS host from
  // window.location. Development (`next dev`): honor the env var override
  // pointing at a locally running FastAPI. `window` guard covers the static
  // export prerender pass (the hook only runs in effects anyway).
  const devOverride =
    process.env.NODE_ENV === "development"
      ? process.env.NEXT_PUBLIC_LEADERBOARD_SERVICE_URL
      : undefined
  const serviceUrl =
    devOverride ??
    (typeof window !== "undefined"
      ? `${window.location.protocol}//${window.location.host}`
      : "http://localhost:8082")
  const proto = serviceUrl.startsWith("https") ? "wss:" : "ws:"
  const host = serviceUrl.replace(/^https?:\/\//, "")
  const url = new URL("/api/v1/leaderboard/live-stream", `${proto}//${host}`)
  if (token) url.searchParams.set("token", token)
  return url.toString()
}

function distinctSorted(values: string[]): string[] {
  return Array.from(new Set(values)).sort((a, b) => a.localeCompare(b))
}

function patchActiveRow(
  rows: LivePositionEntry[],
  mutation: ActiveMutation,
  historicalDeltas: Record<string, number> | undefined,
  historicalAtPositionsNext: Record<string, number> | undefined,
  historicalAtPositionsAtCrossing: Record<string, number> | undefined,
): LivePositionEntry[] {
  let changed = false
  const deltas = historicalDeltas ?? {}
  const atCrossing = historicalAtPositionsAtCrossing ?? {}
  const hasCrossing = Object.keys(atCrossing).length > 0
  const atPositionsNext = hasCrossing
    ? atCrossing
    : historicalAtPositionsNext ?? {}
  const next = rows.map((row) => {
    if (
      row.is_active &&
      row.driver === mutation.driver &&
      row.track === mutation.track &&
      row.car === mutation.car &&
      row.experiment === mutation.experiment
    ) {
      changed = true
      return {
        ...row,
        current_lap: mutation.current_lap ?? row.current_lap,
        current_lap_time_ms: mutation.current_lap_time_ms,
        last_gate_index: mutation.last_gate_index ?? row.last_gate_index,
        last_gate_state: mutation.last_gate_state ?? row.last_gate_state,
        last_gate_delta_ms:
          mutation.last_gate_delta_ms ?? row.last_gate_delta_ms,
      }
    }
    if (
      !row.is_active &&
      row.track === mutation.track &&
      row.car === mutation.car &&
      row.experiment === mutation.experiment
    ) {
      const newDelta = deltas[row.driver]
      const newAtPos = atPositionsNext[row.driver]
      if (newDelta !== undefined || newAtPos !== undefined) {
        changed = true
        return {
          ...row,
          last_gate_index: mutation.last_gate_index ?? row.last_gate_index,
          delta_at_last_gate_ms:
            newDelta !== undefined ? newDelta : row.delta_at_last_gate_ms,
          current_lap_time_ms:
            newAtPos !== undefined ? newAtPos : row.current_lap_time_ms,
        }
      }
    }
    return row
  })
  return changed ? next : rows
}

export function useLiveStream(): UseLiveStreamResult {
  const { token, isLoading } = useQuixAuth()
  const [rows, setRows] = useState<LivePositionEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<Error | null>(null)
  const [isLive, setIsLive] = useState(false)
  const [liveCombo, setLiveCombo] = useState<LiveCombo | null>(null)
  const [freezeEvent, setFreezeEvent] = useState<FreezeEvent | null>(null)
  const prevGateIdxRef = useRef<Map<string, number | null>>(new Map())
  const freezeStampRef = useRef(0)
  const tokenRef = useRef<string | null>(token)
  tokenRef.current = token

  useEffect(() => {
    if (isLoading) return
    let cancelled = false
    let ws: WebSocket | null = null
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null
    let attempt = 0

    const connect = () => {
      if (cancelled) return
      const url = buildStreamUrl(tokenRef.current)
      try {
        ws = new WebSocket(url)
      } catch (err) {
        console.warn("[live-stream] WebSocket constructor threw", err)
        setError(err instanceof Error ? err : new Error("WebSocket open failed"))
        scheduleReconnect()
        return
      }

      ws.onopen = () => {
        attempt = 0
      }

      ws.onmessage = (event) => {
        if (cancelled) return
        let parsed: StreamMessage
        try {
          parsed = JSON.parse(event.data) as StreamMessage
        } catch (err) {
          console.warn("[live-stream] bad message payload", err)
          setError(
            err instanceof Error
              ? err
              : new Error("live-stream payload parse failed"),
          )
          return
        }
        if (parsed.type === "snapshot") {
          setRows(parsed.rows ?? [])
          setLoading(false)
          setError(null)
        } else if (parsed.type === "active") {
          const mutation = parsed.row
          const deltas = parsed.historical_deltas
          const atPositionsNext = parsed.historical_at_positions_next
          const atPositionsAtCrossing =
            parsed.historical_at_positions_at_crossing
          setRows((prev) =>
            patchActiveRow(
              prev,
              mutation,
              deltas,
              atPositionsNext,
              atPositionsAtCrossing,
            ),
          )
          const comboKey = `${mutation.driver}|${mutation.track}|${mutation.car}|${mutation.experiment}`
          const prevIdx = prevGateIdxRef.current.get(comboKey) ?? null
          const newIdx = mutation.last_gate_index
          if (
            newIdx !== null &&
            newIdx !== undefined &&
            newIdx !== prevIdx
          ) {
            freezeStampRef.current += 1
            setFreezeEvent({
              crossedAtGate: newIdx,
              activeAtCrossingMs: mutation.current_lap_time_ms,
              activeAtCrossingGateMs:
                mutation.active_at_crossing_ms ??
                mutation.current_lap_time_ms,
              historicalAtCrossing: atPositionsAtCrossing ?? {},
              stamp: freezeStampRef.current,
            })
            // Gate crossing → fire HTTP refetch for updated rank order.
            // Same-origin in production; dev override via getApiUrl().
            const apiBase = getApiUrl()
            fetch(`${apiBase}/api/v1/leaderboard/live-positions`, {
              credentials: "include",
            })
              .then((r) => (r.ok ? r.json() : null))
              .then((data: LivePositionEntry[] | null) => {
                if (data && Array.isArray(data)) setRows(data)
              })
              .catch(() => {
                // Silent — next snapshot will catch us up.
              })
          }
          prevGateIdxRef.current.set(
            comboKey,
            newIdx ?? null,
          )
          setLoading(false)
          setError(null)
        } else if (parsed.type === "active_state") {
          const next = parsed
          setIsLive(next.is_active)
          if (next.is_active && next.experiment && next.track && next.car) {
            setLiveCombo({
              experiment: next.experiment,
              track: next.track,
              car: next.car,
            })
          } else {
            setLiveCombo(null)
          }
        } else if (parsed.type === "ping") {
          // no-op
        } else {
          console.warn(
            "[live-stream] unexpected envelope type:",
            (parsed as { type?: string })?.type,
          )
        }
      }

      ws.onerror = () => {
        setError((prev) => prev ?? new Error("live-stream socket error"))
      }

      ws.onclose = () => {
        ws = null
        scheduleReconnect()
      }
    }

    const scheduleReconnect = () => {
      if (cancelled) return
      const delay =
        RECONNECT_BACKOFF_MS[
          Math.min(attempt, RECONNECT_BACKOFF_MS.length - 1)
        ]
      attempt += 1
      reconnectTimer = setTimeout(connect, delay)
    }

    connect()

    return () => {
      cancelled = true
      if (reconnectTimer) clearTimeout(reconnectTimer)
      if (ws) {
        try {
          ws.close()
        } catch {
          // Ignore
        }
      }
    }
    // Deps INTENTIONALLY exclude `token`. The hook reads the latest
    // token from `tokenRef.current` inside `connect()`, so token rotation
    // is picked up by the next reconnect without tearing down the
    // current connection. Including `token` here caused the effect to
    // re-run on every auth-context update (null → resolved PAT → refresh),
    // closing each WebSocket within seconds of opening it and starving
    // the snapshot delivery to the UI.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isLoading])

  const tracks = useMemo(() => distinctSorted(rows.map((r) => r.track)), [rows])
  const cars = useMemo(() => distinctSorted(rows.map((r) => r.car)), [rows])
  const experiments = useMemo(
    () => distinctSorted(rows.map((r) => r.experiment)),
    [rows],
  )

  return {
    rows,
    tracks,
    cars,
    experiments,
    loading,
    error,
    isLive,
    liveCombo,
    freezeEvent,
  }
}
