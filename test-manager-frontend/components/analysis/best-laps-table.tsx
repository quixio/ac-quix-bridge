"use client"

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
import { cn } from "@/lib/utils"
import {
  COLLAPSED_ROW_COUNT,
  collapseAroundIndex,
  useAnchoredActiveIdx,
} from "@/lib/utils/leaderboard-window"
import type { LivePositionEntry } from "@/types/leaderboard"
import { BestLapCell } from "@/components/analysis/live-positions-table"

/**
 * "Best Laps" table — Rank · Driver · Best Lap.
 *
 * Fed by the same `/live-positions` payload as Live Sector Comparison.
 * Sorted *client-side* by `best_lap_ms` ascending (treating `null` as
 * `+Infinity`) and ranked 1..N fresh, independent of the server-supplied
 * sector-based `rank`.
 *
 * The active driver keeps the `LIVE` badge so they're locatable, and
 * their row gets the accent tint, but there's no Lap-N label here (lap
 * progress lives in the live table) and no color cues — this table only
 * answers "who set the fastest lap?".
 */
export interface BestLapsTableProps {
  rows: LivePositionEntry[]
  collapsed?: boolean
}

interface RankedRow {
  row: LivePositionEntry
  rank: number
}

export function BestLapsTable({ rows, collapsed = false }: BestLapsTableProps) {
  const sortedRanked: RankedRow[] = [...rows]
    .sort((a, b) => {
      const av = a.best_lap_ms ?? Number.POSITIVE_INFINITY
      const bv = b.best_lap_ms ?? Number.POSITIVE_INFINITY
      return av - bv
    })
    .map((row, idx) => ({ row, rank: idx + 1 }))

  const currentActiveIdx = sortedRanked.findIndex((r) => r.row.is_active)
  // Lag the window so the active driver's rank change reads as a *move*
  // first, then the table re-centres around his new position after a
  // short delay (see `useAnchoredActiveIdx`).
  const anchorIdx = useAnchoredActiveIdx(currentActiveIdx)
  const visible = collapsed
    ? collapseAroundIndex(sortedRanked, COLLAPSED_ROW_COUNT, anchorIdx)
    : sortedRanked

  const [bodyRef] = useAutoAnimate<HTMLTableSectionElement>({
    duration: 700,
    easing: "ease-in-out",
  })

  return (
    <div className="w-full">
      <div className="mb-2">
        <h3 className="text-base font-semibold">Best Laps</h3>
        <p className="text-xs text-muted-foreground">
          Updates only when a new personal best is set
        </p>
      </div>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-[80px]">Rank</TableHead>
            <TableHead>Driver</TableHead>
            <TableHead className="text-right tabular-nums">Best Lap</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody ref={bodyRef}>
          {visible.map(({ row, rank }) => (
            <TableRow
              key={`${row.driver}|${row.track}|${row.car}|${row.experiment}`}
              data-testid={`best-lap-row-${row.driver}`}
              data-active={row.is_active ? "true" : "false"}
              className={cn(
                row.is_active &&
                  "border-l-4 border-l-blue-500 bg-blue-500/10 font-medium",
              )}
            >
              <TableCell className="tabular-nums">{rank}</TableCell>
              <TableCell>
                <div className="flex items-center gap-2">
                  <span>{row.driver}</span>
                  {row.is_active && (
                    <Badge
                      data-testid="best-lap-live-badge"
                      variant="default"
                      className="bg-blue-500 text-white hover:bg-blue-500"
                    >
                      LIVE
                    </Badge>
                  )}
                </div>
              </TableCell>
              <TableCell className="text-right tabular-nums">
                <BestLapCell
                  ms={row.best_lap_ms}
                  lapNumber={row.best_lap_number}
                />
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}
