"use client"

import { useMemo } from "react"
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
import { formatLapTime } from "@/lib/utils/format"
import { cn } from "@/lib/utils"
import { useLiveStream } from "@/lib/hooks/use-live-stream"
import type { LiveStreamMutation } from "@/lib/hooks/use-live-stream"
import {
  COLLAPSED_ROW_COUNT,
  collapseAroundIndex,
  useAnchoredActiveIdx,
} from "@/lib/utils/leaderboard-window"
import type { LivePositionEntry } from "@/types/leaderboard"

/**
 * "Live Sector Comparison" table.
 *
 * Columns: Rank · Driver · Best Lap · At Position.
 *
 * Rank comes from the server's sector-based ordering. Rank changes at
 * sector boundaries. The Best Lap cell renders `m:ss.SSS (L{N})` with the
 * lap suffix dimmed via `text-muted-foreground`.
 *
 * "At Position" coloring:
 *   * Non-active rows: rank-vs-active, same as before
 *     (row.rank < active.rank → emerald; > → rose).
 *   * Active row: driven by the server-computed `last_gate_state` —
 *     "ahead" → emerald, "behind" → rose, "neutral" / null → default
 *     text. The state is set by the backend when the active driver
 *     crosses each of the 20 checkpoint gates and stays sticky until
 *     the next crossing.
 *
 * Active-driver clock source: a WebSocket stream
 * (`/api/v1/leaderboard/live-stream`) pushes AC's `iCurrentTime`
 * verbatim at ≤20 Hz. There is no client-side extrapolation — the
 * cell renders exactly what AC's shared memory reports. That fixes
 * two bugs:
 *   * Pre-start "wall-clock advances while car is stationary"
 *     (`iCurrentTime == 0` until the start line is crossed).
 *   * Parked car mid-lap (AC keeps advancing `iCurrentTime`, so the
 *     clock keeps ticking; previously extrapolation produced the same
 *     visual but for the wrong reason).
 *
 * When AC pauses, the source stops sending and the WS stops pushing —
 * the clock naturally freezes at the last value. After 10 s of
 * silence the backend's `STALE_AFTER_S` window expires and the next
 * `/live-positions` poll drops the active row.
 *
 * Historical rows show their ghost-interpolated `current_lap_time_ms`
 * at the active driver's current map position — a true live comparison.
 *
 * `useAutoAnimate` on the `<TableBody>` animates row reorders when the
 * server reshuffles ranks at sector boundaries.
 */

export interface LivePositionsTableProps {
  rows: LivePositionEntry[]
  collapsed?: boolean
}

function mergeActiveWithStream(
  rows: LivePositionEntry[],
  mutation: LiveStreamMutation | null,
): LivePositionEntry[] {
  if (!mutation) return rows
  // The WS message is keyed by (driver, track, car, experiment) — match
  // it against the active row in the polled slice. We don't trust
  // `is_active` alone because the polled row may transiently lag the
  // stream (e.g. a fresh session whose first poll hasn't completed),
  // and we don't want to overwrite the wrong driver's row.
  let matched = false
  const next = rows.map((row) => {
    if (
      !matched &&
      row.is_active &&
      row.driver === mutation.driver &&
      row.track === mutation.track &&
      row.car === mutation.car &&
      row.experiment === mutation.experiment
    ) {
      matched = true
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
  return next
}

export function LivePositionsTable({
  rows,
  collapsed = false,
}: LivePositionsTableProps) {
  const streamMutation = useLiveStream()

  // Stream mutation overrides the active row's per-tick fields. We do
  // the merge before sorting because sorting is by `rank`, which the
  // stream doesn't touch — so ordering is stable across stream updates.
  const merged = useMemo(
    () => mergeActiveWithStream(rows, streamMutation),
    [rows, streamMutation],
  )
  const sorted = useMemo(
    () => [...merged].sort((a, b) => a.rank - b.rank),
    [merged],
  )

  const currentActiveIdx = sorted.findIndex((r) => r.is_active)
  // Anchor the collapsed window so the active driver's rank change is
  // visible *inside* the existing window before it re-centres — the
  // user reads the move, then the table scrolls to follow.
  const anchorIdx = useAnchoredActiveIdx(currentActiveIdx)
  const visible = collapsed
    ? collapseAroundIndex(sorted, COLLAPSED_ROW_COUNT, anchorIdx)
    : sorted
  const [bodyRef] = useAutoAnimate<HTMLTableSectionElement>({
    duration: 700,
    easing: "ease-in-out",
  })
  const active = sorted.find((r) => r.is_active) ?? null
  const activeRank = active?.rank ?? null

  return (
    <div className="w-full">
      <div className="mb-2">
        <h3 className="text-base font-semibold">Live Sector Comparison</h3>
        <p className="text-xs text-muted-foreground">
          Re-ranks at checkpoint gates
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
        <TableBody ref={bodyRef}>
          {visible.map((row, idx) => {
            // Gap math uses the row's own `current_lap_time_ms` so
            // neighbour deltas stay stable between polls. For the
            // active row that value now comes from the WebSocket
            // stream; for historicals it comes from the polled
            // payload (ghost-interpolated server-side).
            const aboveAtPos =
              idx > 0 ? visible[idx - 1].current_lap_time_ms : null
            const belowAtPos =
              idx < visible.length - 1
                ? visible[idx + 1].current_lap_time_ms
                : null
            return (
              <LeaderRow
                key={`${row.driver}|${row.track}|${row.car}|${row.experiment}`}
                row={row}
                activeRank={activeRank}
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

function formatGapMs(deltaMs: number, sign: "+" | "-"): string {
  const abs = Math.abs(deltaMs) / 1000
  return `${sign}${abs.toFixed(3)}`
}

function LeaderRow({
  row,
  activeRank,
  aboveAtPosMs,
  belowAtPosMs,
}: {
  row: LivePositionEntry
  activeRank: number | null
  aboveAtPosMs: number | null
  belowAtPosMs: number | null
}) {
  // Display value: the row's `current_lap_time_ms` straight from the
  // server. For the active row this has been patched by the WebSocket
  // stream in `mergeActiveWithStream` so it reflects AC's current
  // `iCurrentTime` (no extrapolation).
  const atPosLabel = formatLapTime(row.current_lap_time_ms)

  // Colour cue:
  //   * Non-active rows → rank-vs-active (unchanged).
  //   * Active row → server-computed `last_gate_state`: "ahead" → emerald,
  //     "behind" → rose, anything else → default. The state is sticky on
  //     the server between gate crossings.
  let atPosClass = ""
  if (!row.is_active && activeRank != null) {
    if (row.rank < activeRank) atPosClass = "font-semibold text-emerald-400"
    else if (row.rank > activeRank) atPosClass = "font-semibold text-rose-400"
  } else if (row.is_active) {
    if (row.last_gate_state === "ahead")
      atPosClass = "font-semibold text-emerald-400"
    else if (row.last_gate_state === "behind")
      atPosClass = "font-semibold text-rose-400"
  }

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
