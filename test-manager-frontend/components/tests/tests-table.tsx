"use client"

import { useMemo, useState, useEffect, memo } from "react"
import { useRouter, usePathname } from "next/navigation"
import Link from "next/link"
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
import { TestStatusBadge } from "./test-status-badge"
import type { Test } from "@/types/test"
import { ArrowUpDown, Loader2, Download, BarChart3, LineChart } from "lucide-react"
import { useDateFormatter } from "@/lib/hooks/use-date-formatter"
import { useIntegrationsApi } from "@/lib/hooks/use-api"
import { useToast } from "@/lib/hooks/use-toast"
import { downloadCsv } from "@/lib/utils/csv"

interface TestsTableProps {
  data: Test[]
  sorting: SortingState
  onSortingChange: OnChangeFn<SortingState>
}

export const TestsTable = memo(function TestsTable({ data, sorting, onSortingChange }: TestsTableProps) {
  const router = useRouter()
  const pathname = usePathname()
  const [navigatingTestId, setNavigatingTestId] = useState<string | null>(null)
  const [downloadingTestId, setDownloadingTestId] = useState<string | null>(null)
  const { formatDate } = useDateFormatter()
  const integrationsApi = useIntegrationsApi()
  const { toast } = useToast()

  // Reset loading state when navigation completes
  useEffect(() => {
    setNavigatingTestId(null)
  }, [pathname])

  const handleDownload = async (test: Test, event: React.MouseEvent) => {
    // Prevent row click navigation
    event.stopPropagation()

    setDownloadingTestId(test.test_id)
    try {
      // Call API to get test data (returns CSV text directly)
      const csvContent = await integrationsApi.downloadTestData(
        test.test_id,
        test.experiment_id,
        test.environment_id
      )

      // Check if data is empty
      if (!csvContent || csvContent.trim() === "") {
        toast({
          title: "No data available",
          description: "No measurement data found for this test.",
          variant: "destructive",
        })
        return
      }

      // Generate filename with timestamp
      const timestamp = new Date().toISOString().replace(/[:.]/g, "-").slice(0, -5)
      const filename = `test_data_${test.test_id}_${timestamp}.csv`

      // Trigger download
      downloadCsv(csvContent, filename)

      toast({
        title: "Download started",
        description: `Downloading ${filename}`,
      })
    } catch (error) {
      toast({
        title: "Error downloading data",
        description: error instanceof Error ? error.message : "An error occurred",
        variant: "destructive",
      })
    } finally {
      setDownloadingTestId(null)
    }
  }

  const columns = useMemo<ColumnDef<Test>[]>(
    () => [
      {
        accessorKey: "test_id",
        header: ({ column }) => {
          return (
            <Button
              variant="ghost"
              size="sm"
              className="-ml-3 h-8"
              onClick={() => column.toggleSorting(column.getIsSorted() === "asc")}
            >
              Test ID
              <ArrowUpDown className="ml-2 h-4 w-4" />
            </Button>
          )
        },
        cell: ({ row }) => (
          <div className="font-medium">{row.getValue("test_id")}</div>
        ),
      },
      {
        accessorKey: "experiment_id",
        header: ({ column }) => {
          return (
            <Button
              variant="ghost"
              size="sm"
              className="-ml-3 h-8"
              onClick={() => column.toggleSorting(column.getIsSorted() === "asc")}
            >
              Experiment
              <ArrowUpDown className="ml-2 h-4 w-4" />
            </Button>
          )
        },
        cell: ({ row }) => row.getValue("experiment_id"),
      },
      {
        accessorKey: "status",
        header: "Status",
        cell: ({ row }) => <TestStatusBadge status={row.getValue("status")} />,
      },
      {
        accessorKey: "environment_id",
        header: ({ column }) => {
          return (
            <Button
              variant="ghost"
              size="sm"
              className="-ml-3 h-8"
              onClick={() => column.toggleSorting(column.getIsSorted() === "asc")}
            >
              Environment ID
              <ArrowUpDown className="ml-2 h-4 w-4" />
            </Button>
          )
        },
        cell: ({ row }) => row.getValue("environment_id"),
      },
      {
        accessorKey: "driver",
        header: ({ column }) => {
          return (
            <Button
              variant="ghost"
              size="sm"
              className="-ml-3 h-8"
              onClick={() => column.toggleSorting(column.getIsSorted() === "asc")}
            >
              Driver
              <ArrowUpDown className="ml-2 h-4 w-4" />
            </Button>
          )
        },
        cell: ({ row }) => row.getValue("driver"),
      },
      {
        accessorKey: "created_at",
        header: ({ column }) => {
          return (
            <Button
              variant="ghost"
              size="sm"
              className="-ml-3 h-8"
              onClick={() => column.toggleSorting(column.getIsSorted() === "asc")}
            >
              Created
              <ArrowUpDown className="ml-2 h-4 w-4" />
            </Button>
          )
        },
        cell: ({ row }) => formatDate(row.getValue("created_at")),
      },
      {
        id: "actions",
        header: "",
        cell: ({ row }) => {
          const isDownloading = downloadingTestId === row.original.test_id
          const test = row.original
          return (
            <div className="flex items-center">
              {/* Download CSV */}
              <Button
                variant="ghost"
                size="sm"
                onClick={(e) => handleDownload(test, e)}
                disabled={isDownloading}
                className="h-8 w-8 p-0"
                title="Download test data as CSV"
              >
                {isDownloading ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Download className="h-4 w-4" />
                )}
              </Button>

              {/* Go to Data Query */}
              <Link
                href={`/measurements?test_id=${test.test_id}&experiment_id=${test.experiment_id}&environment_id=${test.environment_id}`}
                onClick={(e) => e.stopPropagation()}
              >
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-8 w-8 p-0"
                  title="Go to Data Query"
                >
                  <BarChart3 className="h-4 w-4" />
                </Button>
              </Link>

              {/* Go to Analytics */}
              <Link
                href={`/analytics?test_id=${test.test_id}&experiment_id=${test.experiment_id}&environment_id=${test.environment_id}`}
                onClick={(e) => e.stopPropagation()}
              >
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-8 w-8 p-0"
                  title="Go to Analytics"
                >
                  <LineChart className="h-4 w-4" />
                </Button>
              </Link>
            </div>
          )
        },
      },
    ],
    [formatDate, downloadingTestId, handleDownload]
  )

  const table = useReactTable({
    data,
    columns,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    onSortingChange,
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
            table.getRowModel().rows.map((row) => {
              const isNavigating = navigatingTestId === row.original.test_id
              return (
                <TableRow
                  key={row.id}
                  onClick={() => {
                    if (!isNavigating) {
                      setNavigatingTestId(row.original.test_id)
                      router.push(`/tests/${row.original.test_id}`)
                    }
                  }}
                  className={`cursor-pointer hover:bg-muted/50 ${isNavigating ? 'pointer-events-none opacity-50' : ''}`}
                >
                  {row.getVisibleCells().map((cell, index) => {
                    const isActionsColumn = cell.column.id === "actions"
                    return (
                      <TableCell key={cell.id} className={isActionsColumn ? "py-2" : ""}>
                        {index === 0 && isNavigating ? (
                          <div className="flex items-center gap-2">
                            <Loader2 className="h-4 w-4 animate-spin" />
                            {flexRender(cell.column.columnDef.cell, cell.getContext())}
                          </div>
                        ) : (
                          flexRender(cell.column.columnDef.cell, cell.getContext())
                        )}
                      </TableCell>
                    )
                  })}
                </TableRow>
              )
            })
          ) : (
            <TableRow>
              <TableCell colSpan={columns.length} className="h-24 text-center">
                No tests found.
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>
    </div>
  )
})
