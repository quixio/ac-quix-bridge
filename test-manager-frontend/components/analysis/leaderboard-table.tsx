"use client"

import { memo, useMemo } from "react"
import {
  useReactTable,
  getCoreRowModel,
  flexRender,
  ColumnDef,
} from "@tanstack/react-table"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import type { BestLapEntry } from "@/types/leaderboard"
import { formatLapTime } from "@/lib/utils/format"
import { Trophy } from "lucide-react"
import { cn } from "@/lib/utils"

/**
 * Row shape handed to this presentational component. Rank is computed by
 * the parent (after filtering + sorting) so this table doesn't own sort
 * state — rank IS the canonical order.
 */
export interface RankedLap extends BestLapEntry {
  rank: number
}

interface LeaderboardTableProps {
  data: RankedLap[]
}

export const LeaderboardTable = memo(function LeaderboardTable({
  data,
}: LeaderboardTableProps) {
  const columns = useMemo<ColumnDef<RankedLap>[]>(
    () => [
      {
        accessorKey: "rank",
        header: () => <span className="block text-right">Rank</span>,
        cell: ({ row }) => {
          const rank = row.original.rank
          const isLeader = rank === 1
          return (
            <div
              className={cn(
                "flex items-center justify-end gap-2 tabular-nums",
                isLeader ? "text-warning font-semibold" : "font-medium"
              )}
            >
              {isLeader && (
                <Trophy
                  className="h-3.5 w-3.5"
                  aria-label="Fastest lap"
                />
              )}
              <span>{rank}</span>
            </div>
          )
        },
      },
      {
        accessorKey: "driver",
        header: () => <span>Driver</span>,
        cell: ({ row }) => (
          <span className="font-medium capitalize">{row.original.driver}</span>
        ),
      },
      {
        accessorKey: "best_lap_ms",
        header: () => <span className="block text-right">Best Lap</span>,
        cell: ({ row }) => (
          <span
            className={cn(
              "block text-right font-mono tabular-nums",
              row.original.rank === 1 ? "font-semibold" : ""
            )}
          >
            {formatLapTime(row.original.best_lap_ms)}
          </span>
        ),
      },
    ],
    []
  )

  const table = useReactTable({
    data,
    columns,
    getCoreRowModel: getCoreRowModel(),
  })

  return (
    <div className="rounded-md border">
      <Table>
        <TableHeader>
          {table.getHeaderGroups().map((headerGroup) => (
            <TableRow key={headerGroup.id}>
              {headerGroup.headers.map((header) => {
                const isRank = header.column.id === "rank"
                const isBestLap = header.column.id === "best_lap_ms"
                return (
                  <TableHead
                    key={header.id}
                    className={cn(
                      isRank && "w-20",
                      isBestLap && "w-40"
                    )}
                  >
                    {header.isPlaceholder
                      ? null
                      : flexRender(
                          header.column.columnDef.header,
                          header.getContext()
                        )}
                  </TableHead>
                )
              })}
            </TableRow>
          ))}
        </TableHeader>
        <TableBody>
          {table.getRowModel().rows.length ? (
            table.getRowModel().rows.map((row) => (
              <TableRow key={row.id}>
                {row.getVisibleCells().map((cell) => (
                  <TableCell key={cell.id}>
                    {flexRender(
                      cell.column.columnDef.cell,
                      cell.getContext()
                    )}
                  </TableCell>
                ))}
              </TableRow>
            ))
          ) : (
            <TableRow>
              <TableCell
                colSpan={columns.length}
                className="h-24 text-center text-sm text-muted-foreground"
              >
                <div className="flex flex-col items-center justify-center gap-2">
                  <Trophy className="h-6 w-6 opacity-50" />
                  <span>
                    No recorded laps for this track, car, and experiment
                    combination yet.
                  </span>
                </div>
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>
    </div>
  )
})
