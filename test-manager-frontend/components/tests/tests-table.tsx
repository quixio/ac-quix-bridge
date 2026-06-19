"use client";

import { useMemo, useState, useEffect, memo } from "react";
import { useRouter, usePathname } from "next/navigation";
import Link from "next/link";
import {
  useReactTable,
  getCoreRowModel,
  getSortedRowModel,
  flexRender,
  ColumnDef,
  SortingState,
  OnChangeFn,
  RowData,
} from "@tanstack/react-table";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Button } from "@/components/ui/button";
import type { Test } from "@/types/test";
import { ArrowUpDown, Loader2, TrendingUp, Database } from "lucide-react";
import { useDateFormatter } from "@/lib/hooks/use-date-formatter";
import { cn } from "@/lib/utils/cn";

declare module "@tanstack/react-table" {
  interface ColumnMeta<TData extends RowData, TValue> {
    className?: string;
  }
}

interface TestsTableProps {
  data: Test[];
  sorting: SortingState;
  onSortingChange: OnChangeFn<SortingState>;
}

export const TestsTable = memo(function TestsTable({
  data,
  sorting,
  onSortingChange,
}: TestsTableProps) {
  const router = useRouter();
  const pathname = usePathname();
  const [navigatingTestId, setNavigatingTestId] = useState<string | null>(null);
  const { formatDate } = useDateFormatter();
  // Reset loading state when navigation completes
  useEffect(() => {
    setNavigatingTestId(null);
  }, [pathname]);

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
              onClick={() =>
                column.toggleSorting(column.getIsSorted() === "asc")
              }
            >
              Test ID
              <ArrowUpDown className="ml-2 h-4 w-4" />
            </Button>
          );
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
              onClick={() =>
                column.toggleSorting(column.getIsSorted() === "asc")
              }
            >
              Experiment
              <ArrowUpDown className="ml-2 h-4 w-4" />
            </Button>
          );
        },
        cell: ({ row }) => row.getValue("experiment_id"),
        meta: { className: "hidden lg:table-cell" },
      },
      {
        accessorKey: "environment_id",
        header: ({ column }) => {
          return (
            <Button
              variant="ghost"
              size="sm"
              className="-ml-3 h-8"
              onClick={() =>
                column.toggleSorting(column.getIsSorted() === "asc")
              }
            >
              Environment
              <ArrowUpDown className="ml-2 h-4 w-4" />
            </Button>
          );
        },
        cell: ({ row }) =>
          row.original.environment_name || row.getValue("environment_id"),
      },
      {
        accessorKey: "driver",
        header: ({ column }) => {
          return (
            <Button
              variant="ghost"
              size="sm"
              className="-ml-3 h-8"
              onClick={() =>
                column.toggleSorting(column.getIsSorted() === "asc")
              }
            >
              Driver
              <ArrowUpDown className="ml-2 h-4 w-4" />
            </Button>
          );
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
              onClick={() =>
                column.toggleSorting(column.getIsSorted() === "asc")
              }
            >
              Created
              <ArrowUpDown className="ml-2 h-4 w-4" />
            </Button>
          );
        },
        cell: ({ row }) => formatDate(row.getValue("created_at")),
      },
      {
        id: "actions",
        header: "",
        cell: ({ row }) => {
          const test = row.original;
          return (
            <div className="flex items-center">
              <Link
                href={`/analysis?tab=compare&test_id=${test.test_id}`}
                onClick={(e) => e.stopPropagation()}
              >
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-8 w-8 p-0"
                  title="Analyze telemetry"
                >
                  <TrendingUp className="h-4 w-4" />
                </Button>
              </Link>
              <Link
                href={`/lakehouse?test_id=${test.test_id}`}
                onClick={(e) => e.stopPropagation()}
              >
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-8 w-8 p-0"
                  title="View in Lakehouse"
                >
                  <Database className="h-4 w-4" />
                </Button>
              </Link>
            </div>
          );
        },
      },
    ],
    [formatDate],
  );

  const table = useReactTable({
    data,
    columns,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    onSortingChange,
    state: {
      sorting,
    },
  });

  return (
    <div className="rounded-md border">
      <Table>
        <TableHeader>
          {table.getHeaderGroups().map((headerGroup) => (
            <TableRow key={headerGroup.id}>
              {headerGroup.headers.map((header) => (
                <TableHead
                  key={header.id}
                  className={header.column.columnDef.meta?.className}
                >
                  {header.isPlaceholder
                    ? null
                    : flexRender(
                        header.column.columnDef.header,
                        header.getContext(),
                      )}
                </TableHead>
              ))}
            </TableRow>
          ))}
        </TableHeader>
        <TableBody>
          {table.getRowModel().rows?.length ? (
            table.getRowModel().rows.map((row) => {
              const isNavigating = navigatingTestId === row.original.test_id;
              return (
                <TableRow
                  key={row.id}
                  onClick={() => {
                    if (!isNavigating) {
                      setNavigatingTestId(row.original.test_id);
                      router.push(`/tests/${row.original.test_id}`);
                    }
                  }}
                  className={`cursor-pointer hover:bg-muted/50 ${
                    isNavigating ? "pointer-events-none opacity-50" : ""
                  }`}
                >
                  {row.getVisibleCells().map((cell, index) => {
                    const isActionsColumn = cell.column.id === "actions";
                    return (
                      <TableCell
                        key={cell.id}
                        className={cn(
                          cell.column.columnDef.meta?.className,
                          isActionsColumn && "py-2",
                        )}
                      >
                        {index === 0 && isNavigating ? (
                          <div className="flex items-center gap-2">
                            <Loader2 className="h-4 w-4 animate-spin" />
                            {flexRender(
                              cell.column.columnDef.cell,
                              cell.getContext(),
                            )}
                          </div>
                        ) : (
                          flexRender(
                            cell.column.columnDef.cell,
                            cell.getContext(),
                          )
                        )}
                      </TableCell>
                    );
                  })}
                </TableRow>
              );
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
  );
});
