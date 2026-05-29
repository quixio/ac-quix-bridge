"use client"

/**
 * Leaderboard live-stream hook.
 *
 * Single source of truth for the leaderboard tab. Opens a WebSocket to
 * `/api/v1/leaderboard/live-stream` and exposes the same shape the old
 * `useLivePositions` polling hook produced:
 *
 *   { rows, tracks, cars, experiments, loading, error }
 *
 * Wire protocol (tagged envelope):
 *   - `{ "type": "snapshot", "rows": [LivePositionEntry, ...] }`
 *     Sent once on connect AND whenever the backend's gate-vectors cache
 *     refreshes (i.e. historicals changed). Replaces `rows` entirely.
 *   - `{ "type": "active", "row": {...} }`
 *     Per-tick mutation of the active driver (~20 Hz). Patches the
 *     single matching row in `rows` by `(driver, track, car, experiment)`.
 *
 * Why a WebSocket instead of polling:
 *   - AC's `iCurrentTime` is in every Kafka tick at 60 Hz — the backend
 *     consumer already has the freshest value. Pulling for it via HTTP
 *     adds 8 s of latency on top of an already-fresh source of truth.
 *   - Client-side extrapolation (the previous approach) ticked the clock
 *     while the car was stationary at the grid (`iCurrentTime == 0`) and
 *     produced plausible-but-wrong numbers for parked cars mid-lap.
 *
 * Reconnect strategy: exponential backoff 1 s → 2 s → 4 s → 10 s ceiling.
 * On reconnect we keep `rows` until the fresh snapshot arrives — that
 * avoids a flicker to "no rows" mid-reconnect.
 *
 * Auth: bearer token via `?token=` query param. Browsers can't set
 * arbitrary headers on a WebSocket handshake, so this is the standard
 * pattern (matches `telemetry-dashboard`).
 */

import { useEffect, useMemo, useRef, useState } from "react"

import { useQuixAuth } from "@/lib/contexts/quix-auth-context"
import type { LivePositionEntry } from "@/types/leaderboard"

export interface UseLiveStreamResult {
  rows: LivePositionEntry[]
  tracks: string[]
  cars: string[]
  experiments: string[]
  loading: boolean
  error: Error | null
}

interface SnapshotMessage {
  type: "snapshot"
  rows: LivePositionEntry[]
}

/**
 * Per-tick mutation payload (active row only). Strict subset of
 * `LivePositionEntry` — historicals, best_lap_ms, rank etc. only land
 * via snapshots.
 */
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
}

interface ActiveMessage {
  type: "active"
  row: ActiveMutation
}

/**
 * Server-side idle keepalive — broadcast every ~25 s to keep the Quix
 * ingress from closing an otherwise-quiet socket. No payload semantics;
 * the hook just ignores it.
 */
interface PingMessage {
  type: "ping"
}

type StreamMessage = SnapshotMessage | ActiveMessage | PingMessage

const RECONNECT_BACKOFF_MS = [1000, 2000, 4000, 10000]

function buildStreamUrl(token: string | null): string {
  // Next.js rewrites proxy `/api/v1/*` to the backend, including WS
  // upgrades — so we open the WebSocket on the same origin the page
  // was served from. `wss:` when the page is on https; otherwise `ws:`.
  const proto =
    typeof window !== "undefined" && window.location.protocol === "https:"
      ? "wss:"
      : "ws:"
  const host =
    typeof window !== "undefined" ? window.location.host : "localhost"
  const url = new URL("/api/v1/leaderboard/live-stream", `${proto}//${host}`)
  if (token) {
    url.searchParams.set("token", token)
  }
  return url.toString()
}

function distinctSorted(values: string[]): string[] {
  return Array.from(new Set(values)).sort((a, b) => a.localeCompare(b))
}

/**
 * Patch the matching active row in `rows` with a mutation. Returns the
 * same array reference when no row matched (React skips the re-render
 * on identity equality) and a new array when one did.
 *
 * Match key is `(driver, track, car, experiment)`. We require
 * `is_active === true` on the polled row as a safety net — the WS
 * shouldn't deliver mutations for non-active rows, but if it does
 * we'd rather drop them than silently overwrite a historical.
 */
function patchActiveRow(
  rows: LivePositionEntry[],
  mutation: ActiveMutation,
): LivePositionEntry[] {
  let changed = false
  const next = rows.map((row) => {
    if (
      !changed &&
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
    return row
  })
  return changed ? next : rows
}

export function useLiveStream(): UseLiveStreamResult {
  const { token, isLoading } = useQuixAuth()
  const [rows, setRows] = useState<LivePositionEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<Error | null>(null)
  // Hold the latest token in a ref so the reconnect loop closure doesn't
  // capture a stale value. We DO want a full re-subscription when the
  // token actually changes, so the main effect's dep array still
  // includes `token` — the ref is only for the inner closure.
  const tokenRef = useRef<string | null>(token)
  tokenRef.current = token

  useEffect(() => {
    // Wait for the auth context to settle before opening — opening with
    // a `null` token and then reopening with the real token a tick later
    // produces a guaranteed-failed handshake and a spurious reconnect.
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
        // Reset the backoff so the next disconnect starts fresh. We
        // don't clear `error` here — we wait for the first valid
        // message so a server that opens then immediately closes
        // doesn't flicker the error state on/off.
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
          // Bind `mutation` outside the setter so the callback closes
          // over a narrowed reference (TS widens `parsed` back to the
          // union once it crosses the function boundary).
          const mutation = parsed.row
          setRows((prev) => patchActiveRow(prev, mutation))
          // An `active` message before the first snapshot can't happen
          // (the server snapshot-first ordering guarantees it), but if
          // somehow we get here, treat the connection as established.
          setLoading(false)
          setError(null)
        } else if (parsed.type === "ping") {
          // Server-side idle keepalive — purely traffic, no state change.
          // We deliberately don't even touch `error` / `loading` here so
          // a ping can never paper over a real protocol issue.
        } else {
          console.warn(
            "[live-stream] unexpected envelope type:",
            (parsed as { type?: string })?.type,
          )
        }
      }

      ws.onerror = () => {
        // Don't act here — `onclose` always follows and is the place
        // where we schedule the reconnect. We DO record an error so
        // the UI can surface "trying to reconnect" state if it wants.
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
          // Ignore — the socket is already in some failure state.
        }
      }
    }
  }, [token, isLoading])

  // Derive filter dropdown lists from the latest snapshot — same shape
  // `useLivePositions` produced. The memoisation key is `rows`; a no-op
  // `active` patch (no row matched) returns the same reference so these
  // memos won't recompute on every tick.
  const tracks = useMemo(() => distinctSorted(rows.map((r) => r.track)), [rows])
  const cars = useMemo(() => distinctSorted(rows.map((r) => r.car)), [rows])
  const experiments = useMemo(
    () => distinctSorted(rows.map((r) => r.experiment)),
    [rows],
  )

  return { rows, tracks, cars, experiments, loading, error }
}
