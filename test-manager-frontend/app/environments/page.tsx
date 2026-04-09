"use client"

import { Suspense, useState, useCallback } from "react"
import { useSearchParams, useRouter } from "next/navigation"
import { SortingState } from "@tanstack/react-table"
import { MainLayout } from "@/components/layout/main-layout"
import { NavigationButton } from "@/components/ui/navigation-button"
import { EnvironmentsTableNew } from "@/components/environments/environments-table-new"
import { EmptyState } from "@/components/shared/empty-state"
import { Pagination } from "@/components/shared/pagination"
import { Skeleton } from "@/components/ui/skeleton"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { useEnvironments } from "@/lib/hooks/use-environments"
import { EnvironmentStatus } from "@/types/environment"
import { Plus, Server } from "lucide-react"

function EnvironmentsPageContent() {
  const router = useRouter()
  const searchParams = useSearchParams()

  const [filters, setFilters] = useState({
    status: searchParams.get("status") as EnvironmentStatus | undefined,
    q: searchParams.get("q") || undefined,
  })

  const [sorting, setSorting] = useState<SortingState>([{ id: "environment_id", desc: false }])

  const {
    environments,
    loading,
    error,
    refetch,
    page,
    pageSize,
    total,
    totalPages,
    goToPage,
    changePageSize,
  } = useEnvironments(filters)

  const updateFilters = useCallback((newFilters: typeof filters) => {
    setFilters(newFilters)
    const params = new URLSearchParams()
    Object.entries(newFilters).forEach(([k, v]) => {
      if (v) params.set(k, v)
    })
    router.push(`/environments?${params.toString()}`)
  }, [router])

  const handleSearch = useCallback((value: string) => {
    updateFilters({ ...filters, q: value || undefined })
  }, [filters, updateFilters])

  const handleStatusChange = useCallback((value: string) => {
    updateFilters({ ...filters, status: value === "all" ? undefined : value as EnvironmentStatus })
  }, [filters, updateFilters])

  const handleClearFilters = useCallback(() => {
    updateFilters({ status: undefined, q: undefined })
  }, [updateFilters])

  return (
    <MainLayout>
      <div className="max-w-7xl">
        <div className="mb-6 flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold tracking-tight">Environments</h1>
            <p className="text-muted-foreground">
              Manage test environments and locations
            </p>
          </div>
          <NavigationButton href="/environments/add">
            <Plus className="mr-2 h-4 w-4" />
            Add Environment
          </NavigationButton>
        </div>

        <div className="space-y-4">
          <div className="flex gap-4">
            <Input
              placeholder="Search environments..."
              defaultValue={filters.q}
              onChange={(e) => handleSearch(e.target.value)}
              className="max-w-sm"
            />
            <Select value={filters.status || "all"} onValueChange={handleStatusChange}>
              <SelectTrigger className="w-[180px]">
                <SelectValue placeholder="All Statuses" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All Statuses</SelectItem>
                <SelectItem value={EnvironmentStatus.ACTIVE}>Active</SelectItem>
                <SelectItem value={EnvironmentStatus.INACTIVE}>Inactive</SelectItem>
              </SelectContent>
            </Select>
          </div>

          {loading ? (
            <div className="space-y-3">
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-64 w-full" />
            </div>
          ) : error ? (
            <EmptyState
              icon={<Server className="h-12 w-12" />}
              title="Failed to load environments"
              description={error.message}
              action={{ label: "Retry", onClick: refetch }}
            />
          ) : environments.length === 0 ? (
            <EmptyState
              icon={<Server className="h-12 w-12" />}
              title="No environments found"
              description={filters.q || filters.status
                ? "No environments match your filters. Try adjusting your criteria."
                : "Get started by adding your first environment."
              }
              action={filters.q || filters.status
                ? { label: "Clear Filters", onClick: handleClearFilters }
                : { label: "Add Environment", onClick: () => router.push("/environments/add") }
              }
            />
          ) : (
            <>
              <EnvironmentsTableNew data={environments} sorting={sorting} onSortingChange={setSorting} />
              {total > 0 && (
                <Pagination
                  page={page}
                  pageSize={pageSize}
                  total={total}
                  totalPages={totalPages}
                  onPageChange={goToPage}
                  onPageSizeChange={changePageSize}
                />
              )}
            </>
          )}
        </div>
      </div>
    </MainLayout>
  )
}

export default function EnvironmentsPage() {
  return (
    <Suspense fallback={<div className="flex items-center justify-center h-screen"><div>Loading...</div></div>}>
      <EnvironmentsPageContent />
    </Suspense>
  )
}
