"use client"

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { BestLapCell } from "@/components/analysis/live-positions-table"

/**
 * "Best Laps" table — Rank · Driver · Best Lap.
 *
 * Step 1.5 shape: fed directly by `/api/v1/leaderboard/best-laps`. The
 * server returns rows already sorted ascending by `best_lap_ms`; this
 * component just numbers them 1..N.
 *
 * No live / active highlighting in this step — Live Sector Comparison
 * owns the live driver state. Re-wiring the LIVE badge / colour cues
 * will land in Step 2 once the WebSocket gate is back on.
 */

export interface BestLapRow {
  driver: string
  best_lap_ms: number
}

export interface BestLapsTableProps {
  rows: BestLapRow[]
}

export function BestLapsTable({ rows }: BestLapsTableProps) {
  return (
    <div className="w-full">
      <div className="mb-2">
        <h3 className="text-base font-semibold">Best Laps</h3>
        <p className="text-xs text-muted-foreground">
          Per-driver fastest lap for the selected experiment / track / car
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
        <TableBody>
          {rows.map((row, idx) => (
            <TableRow
              key={`${row.driver}|${idx}`}
              data-testid={`best-lap-row-${row.driver}`}
            >
              <TableCell className="tabular-nums">{idx + 1}</TableCell>
              <TableCell>{row.driver}</TableCell>
              <TableCell className="text-right tabular-nums">
                <BestLapCell ms={row.best_lap_ms} lapNumber={null} />
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}
