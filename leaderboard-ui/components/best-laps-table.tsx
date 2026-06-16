"use client"

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { BestLapCell } from "@/components/live-positions-table"

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
