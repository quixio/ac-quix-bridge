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

export interface LiveCombo {
  experiment: string
  track: string
  car: string
}

/**
 * Synchronized blue-freeze event (spec §4.3). Fires every time the
 * active driver crosses a new gate; carries the values BOTH the active
 * row and every historical row must show during the 3 s freeze window.
 *
 * Identity (`event` ref) changes on every crossing — pass it as a
 * useEffect dep in the consumer to trigger the freeze state machine.
 */
export interface FreezeEvent {
  /** The gate index just crossed (0..9). */
  crossedAtGate: number
  /** Active driver's iCurrentTime at the moment of crossing (= the
   * server-stamped `gate_times_ms[crossedAtGate]`). */
  activeAtCrossingMs: number
  /** `{driver_display_name: gate_vector[crossedAtGate]}` — each
   * historical's cumulative time at the just-crossed gate. */
  historicalAtCrossing: Record<string, number>
  /** Monotonic stamp so two crossings to the same gate index (e.g.
   * after a lap rollover that bumps i* from 9 → null → 0) still produce
   * a distinct event identity. */
  stamp: number
}

export interface UseLiveStreamResult {
  rows: LivePositionEntry[]
  tracks: string[]
  cars: string[]
  experiments: string[]
  loading: boolean
  error: Error | null
  /** True iff the backend most recently saw a non-stale AC session.
   * Flips false on `active_state.is_active=false`; the backend uses a
   * 20 s hysteresis vs. the 10 s active-row stale window so a quick
   * pause doesn't flicker the toggle. */
  isLive: boolean
  /** `(experiment, track, car)` the live driver is currently on, or
   * `null` when `isLive === false`. Used by `LeaderboardTab` to drive
   * the right-table fetch when Follow-Live is ON. */
  liveCombo: LiveCombo | null
  /** Latest gate-crossing event, or `null` before the first crossing
   * of the current connection. Drives the synchronized 3 s blue-freeze
   * for the active row AND every historical row in lockstep. */
  freezeEvent: FreezeEvent | null
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
  /** Per-historical inline deltas keyed by display-case driver name
   * (spec §7.2). Frontend applies each delta to the matching
   * `(driver, track, car, experiment)` historical row by patching
   * `delta_at_last_gate_ms`. */
  historical_deltas?: Record<string, number>
  /** Per-historical cumulative time at the NEXT gate the active is
   * racing toward — `gate_vector[i*+1]` (clamped). Patched into each
   * historical row's `current_lap_time_ms` for live-mode display
   * (spec §4.2 / §4.3). Empty between gate crossings. */
  historical_at_positions_next?: Record<string, number>
  /** Per-historical cumulative time AT the just-crossed gate —
   * `gate_vector[i*]`. NOT patched into rows; the table reads it from
   * the FreezeEvent during the 3 s blue freeze window (spec §3.4). */
  historical_at_positions_at_crossing?: Record<string, number>
}

/**
 * Server-side idle keepalive — broadcast every ~25 s to keep the Quix
 * ingress from closing an otherwise-quiet socket. No payload semantics;
 * the hook just ignores it.
 */
interface PingMessage {
  type: "ping"
}

/**
 * Active-stream transition envelope (spec §5.1). Sent on connect (with
 * the current state) and on every transition: idle→active, combo
 * change while active, active→idle.
 */
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
 * Patch the matching active row in `rows` with a mutation, AND apply
 * the per-historical `historical_deltas` map to every historical row
 * in the same group (spec §7.2).
 *
 * Match key for the active row: `(driver, track, car, experiment)`.
 * Match key for the historical deltas: same group + display-case
 * driver name (the backend has already resolved the fold→display
 * mapping before publishing, so frontend-side matching is exact
 * equality).
 *
 * Returns the same array reference when nothing matched (React skips
 * the re-render on identity equality) and a new array when something
 * did.
 */
function patchActiveRow(
  rows: LivePositionEntry[],
  mutation: ActiveMutation,
  historicalDeltas: Record<string, number> | undefined,
  historicalAtPositionsNext: Record<string, number> | undefined,
  historicalAtPositionsAtCrossing: Record<string, number> | undefined,
): LivePositionEntry[] {
  let changed = false
  const deltas = historicalDeltas ?? {}
  // Spec §3.4: at a gate crossing, the WS active envelope carries BOTH
  // the at-crossing (gate-N) and next-gate (gate-(N+1)) maps. The 3 s
  // blue freeze (which fires on this same WS message) needs gate-N to
  // be the rendered value. If we patch rows to gate-(N+1) here, the
  // brief 1-frame gap before freezeState catches up shows next-gate
  // times — exactly Ludvik's complaint. Patch to AT-CROSSING when
  // present; HTTP refetch (~100 ms later) will bring gate-(N+1) before
  // the freeze expires.
  const atCrossing = historicalAtPositionsAtCrossing ?? {}
  const hasCrossing = Object.keys(atCrossing).length > 0
  const atPositionsNext = hasCrossing
    ? atCrossing
    : historicalAtPositionsNext ?? {}
  const next = rows.map((row) => {
    // Active-row patch path.
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
    // Historical-row patch path: same group, non-active, name in the
    // deltas / next-position map. We also write through `last_gate_index`
    // so the delta column always points at the active driver's currently-
    // crossed gate even between full snapshots.
    //
    // Spec §3.4 / §4.3: `historical_at_positions_next` carries
    // `gate_vector[i*+1]` — the LIVE-mode display value. The at-crossing
    // value is NOT patched into rows; the table's freeze state machine
    // reads it from the FreezeEvent.
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
  // `isLive` and `liveCombo` follow the latest `active_state` envelope.
  // Defaulted to false / null until the server sends the first one (the
  // backend sends one immediately after the snapshot on connect, so the
  // gap is sub-second in practice).
  const [isLive, setIsLive] = useState(false)
  const [liveCombo, setLiveCombo] = useState<LiveCombo | null>(null)
  // Latest gate-crossing snapshot. New identity on every crossing so the
  // table's effect dep array fires; spec §4.3 freeze state machine.
  const [freezeEvent, setFreezeEvent] = useState<FreezeEvent | null>(null)
  // Previous gate index per active combo key. Kept in a ref so the
  // message handler stays a pure function over inputs without re-
  // rendering on every active envelope.
  const prevGateIdxRef = useRef<Map<string, number | null>>(new Map())
  const freezeStampRef = useRef(0)
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
          // Bind locals outside the setter so the callback closes over
          // narrowed references (TS widens `parsed` back to the union
          // once it crosses the function boundary).
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
          // Detect a fresh gate crossing per (driver, track, car, exp)
          // identity. A change in `last_gate_index` from one non-null
          // value to a different non-null value is a crossing; the
          // first non-null (after lap rollover or first snapshot) is
          // NOT a crossing — there's no preceding gate to capture.
          const comboKey = `${mutation.driver}|${mutation.track}|${mutation.car}|${mutation.experiment}`
          const prevIdx = prevGateIdxRef.current.get(comboKey) ?? null
          const newIdx = mutation.last_gate_index
          if (
            newIdx !== null &&
            newIdx !== undefined &&
            prevIdx !== null &&
            prevIdx !== undefined &&
            newIdx !== prevIdx
          ) {
            freezeStampRef.current += 1
            setFreezeEvent({
              crossedAtGate: newIdx,
              activeAtCrossingMs: mutation.current_lap_time_ms,
              historicalAtCrossing: atPositionsAtCrossing ?? {},
              stamp: freezeStampRef.current,
            })
            // Gate crossing → backend's rank_group has reshuffled ranks,
            // but WS active mutations don't carry the full rows list.
            // Fire an HTTP refetch so rank order updates within ~100 ms
            // instead of waiting for the next best-laps refresh snapshot.
            fetch("/api/v1/leaderboard/live-positions", {
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
          // An `active` message before the first snapshot can't happen
          // (the server snapshot-first ordering guarantees it), but if
          // somehow we get here, treat the connection as established.
          setLoading(false)
          setError(null)
        } else if (parsed.type === "active_state") {
          // Drive the dual-mode toggle. We trust the server's
          // hysteresis (20 s vs. 10 s) so we don't add any client-side
          // debouncing — the envelope already represents a real
          // transition.
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
