"use client"

/**
 * Active-driver live stream hook.
 *
 * Opens a WebSocket to `/api/v1/leaderboard/live-stream` and exposes the
 * latest per-tick active-driver mutation. The mutation overrides the
 * polled `LivePositionEntry` for the active row until the next poll
 * merges in fresh historicals.
 *
 * Why a WebSocket instead of faster polling:
 *   * AC's `iCurrentTime` is in every Kafka tick at 60 Hz — the backend
 *     consumer already has the freshest value. Polling for it adds 8 s
 *     of latency on top of an already-fresh source of truth.
 *   * Client-side extrapolation (the previous approach) ticked the clock
 *     while the car was stationary at the grid (`iCurrentTime == 0`) and
 *     produced plausible-but-wrong numbers for parked cars mid-lap.
 *
 * Reconnect strategy: exponential backoff 1 s → 2 s → 4 s → 10 s
 * ceiling. On reconnect we drop the previous mutation — the next
 * polling tick or WS message will repopulate it.
 *
 * Auth: bearer token via `?token=` query param. Browsers can't set
 * arbitrary headers on a WebSocket handshake, so this is the standard
 * pattern (matches `telemetry-dashboard`).
 */

import { useEffect, useRef, useState } from "react"

import { useQuixAuth } from "@/lib/contexts/quix-auth-context"

export interface LiveStreamMutation {
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
  /** `performance.now()` at the moment the message arrived. Used by
   * consumers to invalidate stale mutations after a reconnect blip. */
  receivedAt: number
}

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
  const url = new URL(
    "/api/v1/leaderboard/live-stream",
    `${proto}//${host}`,
  )
  if (token) {
    url.searchParams.set("token", token)
  }
  return url.toString()
}

export function useLiveStream(): LiveStreamMutation | null {
  const { token, isLoading } = useQuixAuth()
  const [mutation, setMutation] = useState<LiveStreamMutation | null>(null)
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
        scheduleReconnect()
        return
      }

      ws.onopen = () => {
        // Reset the backoff so the next disconnect starts fresh.
        attempt = 0
      }

      ws.onmessage = (event) => {
        if (cancelled) return
        try {
          const data = JSON.parse(event.data) as Omit<
            LiveStreamMutation,
            "receivedAt"
          >
          setMutation({
            ...data,
            receivedAt:
              typeof performance !== "undefined"
                ? performance.now()
                : Date.now(),
          })
        } catch (err) {
          console.warn("[live-stream] bad message payload", err)
        }
      }

      ws.onerror = () => {
        // Don't act here — `onclose` always follows and is the place
        // where we schedule the reconnect.
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

  return mutation
}
