"use client"

import { useMemo, useState, memo } from "react"
import {
  useReactTable,
  getCoreRowModel,
  getSortedRowModel,
  flexRender,
  ColumnDef,
  SortingState,
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
import { EnvironmentStatusBadge } from "./environment-status-badge"
import type { MockEnvironment } from "@/lib/data/mock-environments"
import { ArrowUpDown } from "lucide-react"
import { useDateFormatter } from "@/lib/hooks/use-date-formatter"
import { useToast } from "@/lib/hooks/use-toast"

interface EnvironmentsTableProps {
  data: MockEnvironment[]
}

export const EnvironmentsTable = memo(function EnvironmentsTable({ data }: EnvironmentsTableProps) {
  const { formatDate } = useDateFormatter()
  const { toast } = useToast()

  const handleSortClick = () => {
    toast({
      title: "Feature under construction",
      description: "Sorting will be available when this feature is complete.",
    })
  }

  const handleRowClick = (environment: MockEnvironment) => {
    toast({
      title: "Feature under construction",
      description: `Environment details for ${environment.name} will be available soon.`,
    })
  }

  const columns = useMemo<ColumnDef<MockEnvironment>[]>(
    () => [
      {
        accessorKey: "environment_id",
        header: () => {
          return (
            <Button
              variant="ghost"
              size="sm"
              className="-ml-3 h-8"
              onClick={handleSortClick}
            >
              Environment ID
              <ArrowUpDown className="ml-2 h-4 w-4" />
            </Button>
          )
        },
        cell: ({ row }) => (
          <div className="font-medium">{row.getValue("environment_id")}</div>
        ),
      },
      {
        accessorKey: "name",
        header: () => {
          return (
            <Button
              variant="ghost"
              size="sm"
              className="-ml-3 h-8"
              onClick={handleSortClick}
            >
              Name
              <ArrowUpDown className="ml-2 h-4 w-4" />
            </Button>
          )
        },
        cell: ({ row }) => row.getValue("name"),
      },
      {
        accessorKey: "location",
        header: () => {
          return (
            <Button
              variant="ghost"
              size="sm"
              className="-ml-3 h-8"
              onClick={handleSortClick}
            >
              Location
              <ArrowUpDown className="ml-2 h-4 w-4" />
            </Button>
          )
        },
        cell: ({ row }) => row.getValue("location"),
      },
      {
        accessorKey: "status",
        header: "Status",
        cell: ({ row }) => <EnvironmentStatusBadge status={row.getValue("status")} />,
      },
      {
        accessorKey: "capacity",
        header: "Capacity",
        cell: ({ row }) => {
          const capacity = row.getValue("capacity") as number
          return <span>{capacity} {capacity === 1 ? "device" : "devices"}</span>
        },
      },
      {
        accessorKey: "created_at",
        header: () => {
          return (
            <Button
              variant="ghost"
              size="sm"
              className="-ml-3 h-8"
              onClick={handleSortClick}
            >
              Created
              <ArrowUpDown className="ml-2 h-4 w-4" />
            </Button>
          )
        },
        cell: ({ row }) => formatDate(row.getValue("created_at")),
      },
    ],
    [formatDate]
  )

  const [sorting] = useState<SortingState>([])

  const table = useReactTable({
    data,
    columns,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    state: {
      sorting,
    },
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
                    : flexRender(
                        header.column.columnDef.header,
                        header.getContext()
                      )}
                </TableHead>
              ))}
            </TableRow>
          ))}
        </TableHeader>
        <TableBody>
          {table.getRowModel().rows?.length ? (
            table.getRowModel().rows.map((row) => (
              <TableRow
                key={row.id}
                onClick={() => handleRowClick(row.original)}
                className="cursor-pointer hover:bg-muted/50"
              >
                {row.getVisibleCells().map((cell) => (
                  <TableCell key={cell.id}>
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </TableCell>
                ))}
              </TableRow>
            ))
          ) : (
            <TableRow>
              <TableCell colSpan={columns.length} className="h-24 text-center">
                No environments found.
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>
    </div>
  )
})
