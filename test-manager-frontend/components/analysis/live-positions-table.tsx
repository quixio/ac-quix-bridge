"use client"

import { useEffect, useMemo, useRef, useState } from "react"
import { useAutoAnimate } from "@formkit/auto-animate/react"

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { formatLapTime } from "@/lib/utils/format"
import { cn } from "@/lib/utils"
import {
  COLLAPSED_ROW_COUNT,
  collapseAroundIndex,
  useAnchoredActiveIdx,
} from "@/lib/utils/leaderboard-window"
import type { FreezeEvent } from "@/lib/hooks/use-live-stream"
import type { LivePositionEntry } from "@/types/leaderboard"

/**
 * "Live Sector Comparison" table — spec
 * `dev-planning/leaderboard-consolidated/spec.md` §3.
 *
 * Columns: Rank · Driver · Best Lap · At Position.
 *
 * Ranking comes from the server's sticky gate-N metric — the rank cell
 * only changes at gate crossings.
 *
 * "At Position" rendering (spec §3.3 / §3.4):
 *   * Active row, live mode: `row.current_lap_time_ms` (live-ticking
 *     iCurrentTime from the WS) coloured by server's `last_gate_state`:
 *       "ahead" → emerald-400, "behind" → rose-400, else default.
 *   * Active row, frozen mode (≤3 s after a crossing): the value
 *     captured at the moment of crossing, in blue-400.
 *   * Historical row, live mode: `row.current_lap_time_ms` which is the
 *     server's gate_vector[i*+1] (next-gate value, snapped at each
 *     crossing). No colour class — historicals are always default text.
 *   * Historical row, frozen mode: `crossingSnapshot.historicalAtMs[
 *     driver]` (gate_vector[i*]); falls back to the row's value if the
 *     map is missing the driver (cold-cache).
 *
 * Active-row dual gap chips (spec §3.5):
 *   * Red `+X.XXX` = gap behind the row immediately above active (omitted
 *     when active is rank 1).
 *   * Green `-X.XXX` = gap ahead of the row immediately below (omitted
 *     when active is last visible).
 *   Both anchored on `crossingSnapshot.activeAtMs` / `historicalAtMs[
 *   neighbour]` — sticky from the most-recent gate crossing, byte-stable
 *   between crossings. Both render together when active is mid-rank.
 *
 * "See all" toggle (spec §3.2 / §4.4): renders above the table, right-
 * aligned, `variant="outline" size="sm"`. Defaults to collapsed (8 rows).
 *
 * `useAutoAnimate` on the `<TableBody>` animates row reorders.
 */

export interface LivePositionsTableProps {
  rows: LivePositionEntry[]
  /** Synchronized blue-freeze trigger (spec §4.3). `null` until first
   * gate crossing of the current connection. */
  freezeEvent?: FreezeEvent | null
  /** When `false`, render an empty-state instead of the table. */
  isLive?: boolean
}

// Duration (ms) of the blue-freeze window after each gate crossing.
// Override via NEXT_PUBLIC_FREEZE_MS so it can be tuned alongside the
// backend's GATE_COUNT — at 30+ gates the default 3 s stacks until the
// table feels permanently frozen.
const FREEZE_MS = Number.parseInt(
  process.env.NEXT_PUBLIC_FREEZE_MS ?? "3000",
  10,
)

/**
 * Mode of the synchronized blue-freeze state machine (spec §4.3).
 *   * "live"   — the 3 s window has expired (or no crossing yet)
 *                active row live-ticks, historicals show their next-gate
 *                cumulative time, colour comes from `last_gate_state`.
 *   * "frozen" — within 3 s of the latest gate crossing; active row is
 *                blue and pinned to `activeAtCrossingMs`, historicals
 *                pinned to their gate-N cumulative time.
 * The crossing capture itself (active value + per-historical map) lives in
 * `CrossingSnapshot` and is preserved AFTER the freeze expires so the
 * dual gap chips (§3.5) stay sticky between crossings.
 */
type FreezeMode = "live" | "frozen"

interface FreezeState {
  mode: FreezeMode
  /** Monotonic stamp of the crossing this state is locked to; matches
   * `CrossingSnapshot.stamp` while the freeze is active. `0` before any
   * crossing has happened on this connection. */
  stamp: number
}

/**
 * The most-recent gate crossing's captured values. Sticky across freeze
 * transitions — when the 3 s timer expires we DROP the freeze mode but
 * keep this snapshot so the gap chips and historical lookups continue
 * to reference a stable gate-N cumulative time until the next crossing
 * overrides it.
 *
 * Spec §3.5: "Both numbers update only at gate crossings; between
 * crossings they are byte-stable." The chip math reads `activeAtMs`
 * and `historicalAtMs[neighbour]` regardless of freeze mode — i.e.
 * sticky from the last crossing.
 */
interface CrossingSnapshot {
  stamp: number
  /** Active's iCurrentTime at the moment of the most-recent crossing.
   * Used as the frozen "At Position" display value (spec §3.4). */
  activeAtMs: number
  /** Active's server-stamped cumulative time AT the just-crossed gate
   * (= `gate_times_ms[crossedAtGate]`). The reference the dual gap chips
   * (§3.5) compare each historical's `gate_vector[crossedAtGate]`
   * against. Distinct from `activeAtMs` only by a possible single-frame
   * lag; kept separate so the chip math always has an exact reference. */
  activeAtGateMs: number
  /** `{display_driver: gate_vector[crossedAtGate]}` for every historical
   * in the group as of the most-recent crossing. */
  historicalAtMs: Record<string, number>
}

export function LivePositionsTable({
  rows,
  freezeEvent = null,
  isLive = true,
}: LivePositionsTableProps) {
  // Every hook MUST be called before any conditional return so React's
  // hook order stays stable across renders. The `isLive=false` empty
  // state branch lives below the hook setup.
  const sorted = useMemo(
    () => [...rows].sort((a, b) => a.rank - b.rank),
    [rows],
  )

  const currentActiveIdx = sorted.findIndex((r) => r.is_active)
  // Anchor the collapsed window so the active driver's rank change is
  // visible *inside* the existing window before it re-centres — the
  // user reads the move, then the table scrolls to follow.
  const anchorIdx = useAnchoredActiveIdx(currentActiveIdx)

  // "See all" toggle (spec §3.2). Component-local state — refresh resets
  // to collapsed; no localStorage.
  const [expanded, setExpanded] = useState(false)
  const visible = expanded
    ? sorted
    : collapseAroundIndex(sorted, COLLAPSED_ROW_COUNT, anchorIdx)

  const [bodyRef] = useAutoAnimate<HTMLTableSectionElement>({
    duration: 700,
    easing: "ease-in-out",
  })

  // Synchronized blue-freeze (spec §4.3) is split into TWO pieces of state:
  //
  //   * `freezeState` — mode + stamp. Drives the 3 s blue paint on the
  //     active row's "At Position" cell. One setTimeout per crossing
  //     a newer crossing fully overrides (new capture, new 3 s window).
  //
  //   * `crossingSnapshot` — the captured values at the most-recent
  //     crossing. STICKY: preserved even after the 3 s freeze expires
  //     so the dual gap chips (§3.5) and frozen-mode historical lookups
  //     read from one stable source. Each crossing replaces it entirely.
  //
  // Splitting these two means the per-frame display logic always reads
  // historical at-crossing values from `crossingSnapshot` (whether the
  // freeze is active or not) — that closes nitpicker R2-2 (flicker
  // between gate-N and gate-(N+1) on alternating frames) by removing
  // the conditional source-switching from `rowDisplayMs`.
  //
  // Robustness:
  //   * `latestFreezeStampRef` guards against a late setTimeout firing
  //     after a newer crossing has already overridden the freeze.
  //   * `freezeEventRef` is updated on every render so the stamp-keyed
  //     effect can read the latest event identity without including
  //     the object in its dep array (which would re-fire on every
  //     parent render that recreated the FreezeEvent).
  //   * Lap rollover is handled by clearing `freezeState` to "live" the
  //     moment the active row's `last_gate_index` goes null. The
  //     crossingSnapshot is left in place — the prior lap's at-crossing
  //     values stay sticky until the first crossing of the new lap
  //     overrides them; gap chips will still render against the most-
  //     recent comparable reference. Acceptable behaviour at the
  //     boundary; the lap rollover itself is a sub-second event in
  //     practice.
  const [freezeState, setFreezeState] = useState<FreezeState>({
    mode: "live",
    stamp: 0,
  })
  const [crossingSnapshot, setCrossingSnapshot] =
    useState<CrossingSnapshot | null>(null)
  const latestFreezeStampRef = useRef<number>(0)
  const freezeEventRef = useRef<FreezeEvent | null>(null)
  freezeEventRef.current = freezeEvent

  useEffect(() => {
    const ev = freezeEventRef.current
    if (!ev) return
    latestFreezeStampRef.current = ev.stamp
    const stampAtSchedule = ev.stamp
    // Two state updates per crossing — both batched by React 18 inside an
    // effect, so the consumer sees ONE re-render with both new values.
    setCrossingSnapshot({
      stamp: ev.stamp,
      activeAtMs: ev.activeAtCrossingMs,
      activeAtGateMs: ev.activeAtCrossingGateMs ?? ev.activeAtCrossingMs,
      historicalAtMs: ev.historicalAtCrossing,
    })
    setFreezeState({ mode: "frozen", stamp: ev.stamp })
    const t = setTimeout(() => {
      // Late-timer guard: only flip back to live if our scheduled stamp
      // is still the latest. A newer crossing scheduled its own timer.
      if (latestFreezeStampRef.current === stampAtSchedule) {
        setFreezeState({ mode: "live", stamp: stampAtSchedule })
      }
    }, FREEZE_MS)
    return () => clearTimeout(t)
  }, [freezeEvent?.stamp])

  // Lap rollover reset: spec §4.3 mandates restoring live mode when the
  // active driver's `last_gate_index` goes null. We DON'T clear
  // `crossingSnapshot` here — the gap chips lose meaning until the next
  // crossing, but the user shouldn't see a layout flash. The next
  // crossing fully replaces the snapshot.
  const activeRow = sorted.find((r) => r.is_active) ?? null
  const activeLastGateIdx = activeRow?.last_gate_index ?? null
  useEffect(() => {
    if (activeLastGateIdx === null || activeLastGateIdx === undefined) {
      setFreezeState((prev) =>
        prev.mode === "live" ? prev : { mode: "live", stamp: prev.stamp },
      )
    }
  }, [activeLastGateIdx])

  if (!isLive) {
    return (
      <div className="w-full">
        <div className="mb-2">
          <h3 className="text-base font-semibold">Live Sector Comparison</h3>
          <p className="text-xs text-muted-foreground">
            Re-ranks at checkpoint gates
          </p>
        </div>
        <div className="flex items-center gap-2 rounded border border-dashed border-muted-foreground/30 p-4 text-sm text-muted-foreground">
          <span>
            No live session — start an AC session to see live sector deltas.
          </span>
        </div>
      </div>
    )
  }

  return (
    <div className="w-full">
      <div className="mb-2 flex items-end justify-between gap-2">
        <div>
          <h3 className="text-base font-semibold">Live Sector Comparison</h3>
          <p className="text-xs text-muted-foreground">
            Re-ranks at checkpoint gates
          </p>
        </div>
        {sorted.length > COLLAPSED_ROW_COUNT && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => setExpanded((v) => !v)}
            data-testid="see-all-toggle"
          >
            {expanded ? "Show top 8" : "See all"}
          </Button>
        )}
      </div>
      <Table className="table-fixed">
        <TableHeader>
          <TableRow>
            <TableHead className="w-[64px]">Rank</TableHead>
            <TableHead className="w-[240px]">Driver</TableHead>
            <TableHead className="w-[160px] text-right tabular-nums">
              Best Lap
            </TableHead>
            <TableHead className="text-right tabular-nums">
              At Position
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody ref={bodyRef}>
          {visible.map((row, idx) => {
            // Gap math (spec §3.5). Both sides read the per-row
            // `gate_time_at_crossing_ms` field — cumulative time at the
            // active driver's last crossed gate i*. The backend stamps
            // this on every row (historical AND active) so neighbours
            // that arrive via rank-shuffle mid-lap carry the correct
            // value without depending on the sticky `crossingSnapshot`
            // map. Falls back to that map only if the row is missing
            // the field (pre-deploy backend), and to `null` after that
            // — never to `current_lap_time_ms` (= gate i*+1, would
            // re-introduce the N+1 mismatch that suppressed the red
            // chip and inflated the green one).
            const aboveRow = idx > 0 ? visible[idx - 1] : null
            const belowRow =
              idx < visible.length - 1 ? visible[idx + 1] : null
            const aboveAtCrossingMs =
              aboveRow?.gate_time_at_crossing_ms ??
              (aboveRow?.driver != null && crossingSnapshot
                ? crossingSnapshot.historicalAtMs[aboveRow.driver] ?? null
                : null)
            const belowAtCrossingMs =
              belowRow?.gate_time_at_crossing_ms ??
              (belowRow?.driver != null && crossingSnapshot
                ? crossingSnapshot.historicalAtMs[belowRow.driver] ?? null
                : null)
            return (
              <LeaderRow
                key={`${row.driver}|${row.track}|${row.car}|${row.experiment}|${row.is_active ? "live" : "ghost"}`}
                row={row}
                freezeState={freezeState}
                crossingSnapshot={crossingSnapshot}
                aboveAtCrossingMs={aboveAtCrossingMs}
                belowAtCrossingMs={belowAtCrossingMs}
              />
            )
          })}
        </TableBody>
      </Table>
    </div>
  )
}

/** Resolve the "At Position" value a row should display right now.
 *
 * The active row's value depends on freeze mode (live → live timer;
 * frozen → captured `activeAtMs`).
 *
 * Historicals:
 *   * frozen → `gate_time_at_crossing_ms` (gate-i*, per-row, server-
 *     stamped). Falls back to the sticky `historicalAtMs` map, then
 *     to `current_lap_time_ms` only as a last resort. The per-row
 *     value lets a rank-shuffled neighbour mid-lap show its own
 *     correct gate-i* time during the 3 s freeze — without the row
 *     field we'd fall straight through to the gate-i*+1 projection
 *     and the user couldn't visually compare like-for-like with the
 *     frozen active timer.
 *   * live   → `row.current_lap_time_ms` (backend's gate-i*+1
 *     projection, refreshed on every crossing tick via WS patch).
 */
function rowDisplayMs(
  row: LivePositionEntry,
  freezeState: FreezeState,
  crossingSnapshot: CrossingSnapshot | null,
): number {
  if (freezeState.mode === "frozen") {
    if (row.is_active) {
      return crossingSnapshot?.activeAtMs ?? row.current_lap_time_ms
    }
    if (typeof row.gate_time_at_crossing_ms === "number") {
      return row.gate_time_at_crossing_ms
    }
    if (crossingSnapshot != null) {
      const fromMap = crossingSnapshot.historicalAtMs[row.driver]
      if (typeof fromMap === "number") {
        return fromMap
      }
    }
    return row.current_lap_time_ms
  }
  return row.current_lap_time_ms
}

function formatGapMs(deltaMs: number, sign: "+" | "-"): string {
  const abs = Math.abs(deltaMs) / 1000
  return `${sign}${abs.toFixed(3)}`
}

function LeaderRow({
  row,
  freezeState,
  crossingSnapshot,
  aboveAtCrossingMs,
  belowAtCrossingMs,
}: {
  row: LivePositionEntry
  freezeState: FreezeState
  crossingSnapshot: CrossingSnapshot | null
  /** Neighbour-above's `gate_vector[N]` cumulative time at the last
   * crossing, or `null` when no snapshot has been captured yet. Used
   * for the dual gap chip math (spec §3.5). */
  aboveAtCrossingMs: number | null
  /** Neighbour-below's `gate_vector[N]` at the last crossing. */
  belowAtCrossingMs: number | null
}) {
  const isFrozen = freezeState.mode === "frozen"
  const displayMs = rowDisplayMs(row, freezeState, crossingSnapshot)
  const atPosLabel = formatLapTime(displayMs)

  // Colour cue (per Ludvik 2026-06-03): the active row's live timer
  // stays default-white at all times EXCEPT during the 3 s blue freeze
  // at gate crossings. The ahead/behind signal is carried by the dual
  // gap chips next to the time, NOT by the time text itself, so the
  // user can read the time stably without the colour shifting under it.
  // Historicals: always default text colour.
  let atPosClass = ""
  if (row.is_active && isFrozen) {
    atPosClass = "font-semibold text-blue-400"
  }

  // Dual gap chips (spec §3.5). Both reference the LAST crossing's
  // captured values — sticky between crossings, byte-stable. The gap
  // is computed only when BOTH sides have a numeric reference, so a
  // cold-cache pre-first-crossing renders nothing rather than wrong
  // numbers.
  //
  // Round 2 fix (nitpicker R2-3): previously we used `displayMs` for
  // the active reference, which in live mode is the live-ticking
  // `current_lap_time_ms`. That mixed live (active) with sticky
  // gate-N+1 (historicals' display value) and produced a negative
  // `gapAbove` (active's small live timer < historical's gate-N+1
  // ≈ 80s+), suppressing the red chip. Now both sides come from the
  // captured at-crossing snapshot, so chips reflect the gate-N
  // standing and both render simultaneously when active is mid-rank.
  //
  // Regression fix 2026-06-03: the active reference now prefers the
  // server-stamped `activeAtGateMs` (= gate_times_ms[i*]) over the
  // live `activeAtMs`, and falls back to the active row's WS
  // `current_lap_time_ms` when no snapshot exists yet (cold-cache /
  // immediately after a lap rollover, before the first crossing of the
  // new lap is captured). Without that fallback the red `+X.XXX` chip
  // disappeared whenever the snapshot was stale or null.
  const activeRef =
    crossingSnapshot?.activeAtGateMs ??
    crossingSnapshot?.activeAtMs ??
    (row.is_active ? row.current_lap_time_ms : null)
  const gapAbove =
    row.is_active && activeRef != null && aboveAtCrossingMs != null
      ? Math.max(0, activeRef - aboveAtCrossingMs)
      : null
  const gapBelow =
    row.is_active && activeRef != null && belowAtCrossingMs != null
      ? Math.max(0, belowAtCrossingMs - activeRef)
      : null

  return (
    <TableRow
      data-testid={`leader-row-${row.driver}`}
      data-active={row.is_active ? "true" : "false"}
      className={cn(
        row.is_active &&
          "border-l-4 border-l-blue-500 bg-blue-500/10 font-medium",
      )}
    >
      <TableCell className="tabular-nums">{row.rank}</TableCell>
      <TableCell>
        <div className="flex items-center gap-2">
          <span>{row.driver}</span>
          {row.is_active && (
            <>
              <Badge
                data-testid="live-badge"
                variant="default"
                className="bg-blue-500 text-white hover:bg-blue-500"
              >
                LIVE
              </Badge>
              {row.current_lap != null && (
                <span className="inline-block w-[56px] text-xs uppercase tracking-wider tabular-nums text-muted-foreground">
                  Lap {row.current_lap}
                </span>
              )}
            </>
          )}
        </div>
      </TableCell>
      <TableCell className="text-right tabular-nums">
        <BestLapCell ms={row.best_lap_ms} lapNumber={row.best_lap_number} />
      </TableCell>
      <TableCell className="text-right tabular-nums">
        <div className="flex items-center justify-end gap-2">
          <span className={cn(atPosClass)}>{atPosLabel}</span>
          {/* Active-row dual gap: red `+` = behind the row above, green
              `-` = ahead of the row below. Hidden when no neighbour. */}
          {row.is_active && gapAbove != null && gapAbove > 0 && (
            <span className="text-xs font-semibold tabular-nums text-rose-400">
              {formatGapMs(gapAbove, "+")}
            </span>
          )}
          {row.is_active && gapBelow != null && gapBelow > 0 && (
            <span className="text-xs font-semibold tabular-nums text-emerald-400">
              {formatGapMs(gapBelow, "-")}
            </span>
          )}
        </div>
      </TableCell>
    </TableRow>
  )
}

/**
 * Best-lap cell renderer shared with the Best Laps table.
 *
 * Format: `m:ss.SSS` with a dimmed `(L{N})` suffix when the lap number
 * is known. Renders an em dash when the lap time itself is null.
 */
export function BestLapCell({
  ms,
  lapNumber,
}: {
  ms: number | null
  lapNumber: number | null
}) {
  if (ms == null) return <>—</>
  return (
    <>
      <span>{formatLapTime(ms)}</span>
      {lapNumber != null && (
        <span className="ml-1 text-muted-foreground">(L{lapNumber})</span>
      )}
    </>
  )
}
