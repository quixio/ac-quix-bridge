"use client"

import { useEffect, useRef, useState } from "react"

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Badge } from "@/components/ui/badge"
import { formatLapTime } from "@/lib/utils/format"
import { cn } from "@/lib/utils"
import type { LivePositionEntry } from "@/types/leaderboard"

/**
 * "Live Sector Comparison" table — 5 rows per (track, car, experiment) group.
 *
 * Columns: Rank · Driver · Best Lap · At Position.
 *
 * Rank changes at sector boundaries. The Best Lap cell renders
 * `m:ss.SSS (L{N})` with the lap suffix dimmed via `text-muted-foreground`.
 *
 * "At Position" coloring is now driven by **rank vs. the active driver**:
 *   row.rank < active.rank → ranked better → emerald
 *   row.rank > active.rank → ranked worse  → rose
 *   active row             → neutral
 *
 * The active row's At Position cell ticks at 200 ms intervals between
 * polls so the running clock advances visually instead of jumping every
 * 3500 ms. Server payload provides the anchor `current_lap_time_ms` at
 * each poll; locally we add `performance.now() - localT0` to that anchor.
 * Historical rows do *not* tick — they are pure server numbers (ghost
 * estimates re-anchored to the active driver's poll-time position).
 */

// The clock re-renders at jittered intervals in [TICK_MIN_MS, TICK_MAX_MS]
// to read as organic rather than a metronome. Picked roughly around 150 ms.
const TICK_MIN_MS = 100
const TICK_MAX_MS = 180
// On every fresh poll, freeze the running clock for this many ms so the
// user can eyeball-compare the synchronized white At-Position values.
const FREEZE_AFTER_POLL_MS = 3000

export interface LivePositionsTableProps {
  rows: LivePositionEntry[]
}

export function LivePositionsTable({ rows }: LivePositionsTableProps) {
  const sorted = [...rows].sort((a, b) => a.rank - b.rank)
  const active = sorted.find((r) => r.is_active) ?? null
  const activeRank = active?.rank ?? null
  const activeServerMs = active?.current_lap_time_ms ?? 0

  // Anchor for client-side extrapolation. We capture both the latest
  // server-reported elapsed (`serverElapsedMs`) and the local clock at
  // the moment we received it (`localT0`). Display = anchor + (now - t0).
  const anchorRef = useRef<{ serverElapsedMs: number; localT0: number }>({
    serverElapsedMs: activeServerMs,
    localT0:
      typeof performance !== "undefined" ? performance.now() : Date.now(),
  })

  // Re-anchor whenever the server payload changes. If the new server
  // value is *less* than the extrapolated display (lap rollover during
  // the gap between polls), the snap is intentional — we accept the new
  // value immediately rather than smoothing.
  useEffect(() => {
    anchorRef.current = {
      serverElapsedMs: activeServerMs,
      localT0:
        typeof performance !== "undefined" ? performance.now() : Date.now(),
    }
  }, [activeServerMs])

  // Forces a re-render every TICK_INTERVAL_MS so the active cell reads
  // the current `performance.now()` and produces a fresh extrapolated
  // value. The state's value is not consumed directly — it's just a
  // changing identity to trigger React.
  const [, setTickNow] = useState<number>(() =>
    typeof performance !== "undefined" ? performance.now() : Date.now(),
  )
  useEffect(() => {
    if (!active) return
    let cancelled = false
    let timeoutId: ReturnType<typeof setTimeout> | null = null
    const scheduleNext = () => {
      if (cancelled) return
      const jitter =
        TICK_MIN_MS + Math.random() * (TICK_MAX_MS - TICK_MIN_MS)
      timeoutId = setTimeout(() => {
        setTickNow(
          typeof performance !== "undefined" ? performance.now() : Date.now(),
        )
        scheduleNext()
      }, jitter)
    }
    scheduleNext()
    return () => {
      cancelled = true
      if (timeoutId != null) clearTimeout(timeoutId)
    }
  }, [active])

  const nowMs =
    typeof performance !== "undefined" ? performance.now() : Date.now()
  const timeSinceAnchor = nowMs - anchorRef.current.localT0
  // Hold the synchronized server value for FREEZE_AFTER_POLL_MS so the
  // user can read across all rows at the moment of a sector re-rank.
  // After the freeze window, resume extrapolation.
  const isFrozen = timeSinceAnchor < FREEZE_AFTER_POLL_MS
  const extrapolateBy = isFrozen ? 0 : timeSinceAnchor
  const activeDisplayMs = active
    ? Math.max(
        0,
        Math.round(anchorRef.current.serverElapsedMs + extrapolateBy),
      )
    : null

  return (
    <div className="w-full">
      <div className="mb-2">
        <h3 className="text-base font-semibold">Live Sector Comparison</h3>
        <p className="text-xs text-muted-foreground">
          Re-ranks at sector boundaries
        </p>
      </div>
      <Table className="table-fixed">
        <TableHeader>
          <TableRow>
            <TableHead className="w-[64px]">Rank</TableHead>
            <TableHead className="w-[240px]">Driver</TableHead>
            <TableHead className="w-[160px] text-right tabular-nums">
              Best Lap
            </TableHead>
            <TableHead className="text-right tabular-nums">At Position</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {sorted.map((row, idx) => {
            // Gap math uses pure server values (not the extrapolated
            // active clock) so the +/- deltas freeze between polls and
            // only refresh when the server publishes a new rank/snapshot.
            const aboveAtPos =
              idx > 0 ? sorted[idx - 1].current_lap_time_ms : null
            const belowAtPos =
              idx < sorted.length - 1
                ? sorted[idx + 1].current_lap_time_ms
                : null
            return (
              <LeaderRow
                key={`${row.driver}|${row.track}|${row.car}|${row.experiment}`}
                row={row}
                activeRank={activeRank}
                activeDisplayMs={activeDisplayMs}
                isFrozen={isFrozen}
                aboveAtPosMs={aboveAtPos}
                belowAtPosMs={belowAtPos}
              />
            )
          })}
        </TableBody>
      </Table>
    </div>
  )
}

function atPosForRow(
  row: LivePositionEntry,
  activeDisplayMs: number | null,
): number {
  return row.is_active && activeDisplayMs != null
    ? activeDisplayMs
    : row.current_lap_time_ms
}

function formatGapMs(deltaMs: number, sign: "+" | "-"): string {
  const abs = Math.abs(deltaMs) / 1000
  return `${sign}${abs.toFixed(3)}`
}

function LeaderRow({
  row,
  activeRank,
  activeDisplayMs,
  isFrozen,
  aboveAtPosMs,
  belowAtPosMs,
}: {
  row: LivePositionEntry
  activeRank: number | null
  activeDisplayMs: number | null
  isFrozen: boolean
  aboveAtPosMs: number | null
  belowAtPosMs: number | null
}) {
  // Display value: extrapolated for the active row's running clock,
  // raw server number for everyone else.
  const atPosMs = atPosForRow(row, activeDisplayMs)
  const atPosLabel = formatLapTime(atPosMs)

  // Color cue by rank-vs-active for historicals. Active row stays neutral —
  // except during the post-poll freeze window, when its number turns blue
  // to flag "synced snapshot, compare now". Returns to white when ticking
  // resumes.
  let atPosClass = ""
  if (!row.is_active && activeRank != null) {
    if (row.rank < activeRank) atPosClass = "font-semibold text-emerald-400"
    else if (row.rank > activeRank) atPosClass = "font-semibold text-rose-400"
  } else if (row.is_active && isFrozen) {
    atPosClass = "text-blue-400"
  }

  // Gap math uses the row's server-side `current_lap_time_ms` (not the
  // extrapolated display value) so the +/- deltas remain stable between
  // polls and only change when the server re-ranks at a sector boundary.
  const serverAtPosMs = row.current_lap_time_ms
  const gapAbove =
    aboveAtPosMs != null ? Math.max(0, serverAtPosMs - aboveAtPosMs) : null
  const gapBelow =
    belowAtPosMs != null ? Math.max(0, belowAtPosMs - serverAtPosMs) : null

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
          {row.is_active && gapAbove != null && (
            <span className="text-xs font-semibold text-rose-400">
              {formatGapMs(gapAbove, "+")}
            </span>
          )}
          {row.is_active && gapBelow != null && (
            <span className="text-xs font-semibold text-emerald-400">
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
