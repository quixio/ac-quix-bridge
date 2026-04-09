"use client"

import { useMemo, useState, useEffect, memo } from "react"
import { useRouter, usePathname } from "next/navigation"
import {
  useReactTable,
  getCoreRowModel,
  getSortedRowModel,
  flexRender,
  ColumnDef,
  SortingState,
  OnChangeFn,
} from "@tanstack/react-table"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { DeviceStatusBadge } from "./device-status-badge"
import type { Device } from "@/types/device"
import { DeviceCategoryLabels } from "@/types/device"
import { ArrowUpDown, Loader2 } from "lucide-react"
import { useDateFormatter } from "@/lib/hooks/use-date-formatter"

interface DevicesTableProps {
  data: Device[]
  sorting: SortingState
  onSortingChange: OnChangeFn<SortingState>
}

export const DevicesTable = memo(function DevicesTable({ data, sorting, onSortingChange }: DevicesTableProps) {
  const router = useRouter()
  const pathname = usePathname()
  const [navigatingId, setNavigatingId] = useState<string | null>(null)
  const { formatDate } = useDateFormatter()

  useEffect(() => {
    setNavigatingId(null)
  }, [pathname])

  const columns = useMemo<ColumnDef<Device>[]>(
    () => [
      {
        accessorKey: "device_id",
        header: ({ column }) => (
          <Button
            variant="ghost"
            size="sm"
            className="-ml-3 h-8"
            onClick={() => column.toggleSorting(column.getIsSorted() === "asc")}
          >
            Device ID
            <ArrowUpDown className="ml-2 h-4 w-4" />
          </Button>
        ),
        cell: ({ row }) => (
          <div className="font-medium">{row.getValue("device_id")}</div>
        ),
      },
      {
        accessorKey: "category",
        header: ({ column }) => (
          <Button
            variant="ghost"
            size="sm"
            className="-ml-3 h-8"
            onClick={() => column.toggleSorting(column.getIsSorted() === "asc")}
          >
            Category
            <ArrowUpDown className="ml-2 h-4 w-4" />
          </Button>
        ),
        cell: ({ row }) => (
          <Badge variant="outline">
            {DeviceCategoryLabels[row.getValue("category") as keyof typeof DeviceCategoryLabels] || row.getValue("category")}
          </Badge>
        ),
      },
      {
        accessorKey: "name",
        header: ({ column }) => (
          <Button
            variant="ghost"
            size="sm"
            className="-ml-3 h-8"
            onClick={() => column.toggleSorting(column.getIsSorted() === "asc")}
          >
            Name
            <ArrowUpDown className="ml-2 h-4 w-4" />
          </Button>
        ),
        cell: ({ row }) => row.getValue("name"),
      },
      {
        accessorKey: "status",
        header: "Status",
        cell: ({ row }) => <DeviceStatusBadge status={row.getValue("status")} />,
      },
      {
        accessorKey: "created_at",
        header: ({ column }) => (
          <Button
            variant="ghost"
            size="sm"
            className="-ml-3 h-8"
            onClick={() => column.toggleSorting(column.getIsSorted() === "asc")}
          >
            Created
            <ArrowUpDown className="ml-2 h-4 w-4" />
          </Button>
        ),
        cell: ({ row }) => formatDate(row.getValue("created_at")),
      },
    ],
    [formatDate]
  )

  const table = useReactTable({
    data,
    columns,
    state: { sorting },
    onSortingChange,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  })

  return (
    <div className="rounded-md border">
      <Table>
        <TableHeader>
          {table.getHeaderGroups().map((headerGroup) => (
            <TableRow key={headerGroup.id}>
              {headerGroup.headers.map((header) => (
                <TableHead key={header.id}>
                  {header.isPlaceholder
                    ? null
                    : flexRender(header.column.columnDef.header, header.getContext())}
                </TableHead>
              ))}
            </TableRow>
          ))}
        </TableHeader>
        <TableBody>
          {table.getRowModel().rows?.length ? (
            table.getRowModel().rows.map((row) => {
              const deviceId = row.original.device_id
              const isNavigating = navigatingId === deviceId
              return (
                <TableRow
                  key={row.id}
                  className="cursor-pointer hover:bg-muted/50"
                  onClick={() => {
                    setNavigatingId(deviceId)
                    router.push(`/devices/${deviceId}`)
                  }}
                >
                  {row.getVisibleCells().map((cell) => (
                    <TableCell key={cell.id}>
                      {isNavigating && cell.column.id === "device_id" ? (
                        <div className="flex items-center gap-2">
                          <Loader2 className="h-4 w-4 animate-spin" />
                          {flexRender(cell.column.columnDef.cell, cell.getContext())}
                        </div>
                      ) : (
                        flexRender(cell.column.columnDef.cell, cell.getContext())
                      )}
                    </TableCell>
                  ))}
                </TableRow>
              )
            })
          ) : (
            <TableRow>
              <TableCell colSpan={columns.length} className="h-24 text-center">
                No devices found.
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>
    </div>
  )
})
