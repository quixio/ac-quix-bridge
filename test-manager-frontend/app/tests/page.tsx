"use client"

import { Suspense, useState, useCallback } from "react"
import { useSearchParams, useRouter } from "next/navigation"
import nextDynamic from "next/dynamic"
import { SortingState } from "@tanstack/react-table"
import { MainLayout } from "@/components/layout/main-layout"
import { NavigationButton } from "@/components/ui/navigation-button"
import { Skeleton } from "@/components/ui/skeleton"

// Lazy load TestsTable to reduce initial bundle size
const TestsTable = nextDynamic(() => import("@/components/tests/tests-table").then((mod) => ({ default: mod.TestsTable })), {
  loading: () => <Skeleton className="h-96 w-full" />,
  ssr: false,
})
import { TestsFilters } from "@/components/tests/tests-filters"
import { EmptyState } from "@/components/shared/empty-state"
import { Pagination } from "@/components/shared/pagination"
import { useTests } from "@/lib/hooks/use-tests"
import { TestStatus } from "@/types/test"
import { Plus, FileText } from "lucide-react"

// Inner component that uses useSearchParams
function TestsPageContent() {
  const router = useRouter()
  const searchParams = useSearchParams()

  // Get filters from URL
  const [filters, setFilters] = useState({
    environment_id: searchParams.get("environment_id") || undefined,
    experiment_id: searchParams.get("experiment_id") || undefined,
    q: searchParams.get("q") || undefined,
  })

  const [sorting, setSorting] = useState<SortingState>([{ id: "test_id", desc: false }])

  // Fetch tests with filters and pagination
  const {
    tests,
    loading,
    error,
    refetch,
    page,
    pageSize,
    total,
    totalPages,
    goToPage,
    changePageSize,
  } = useTests(filters)

  // Handle filter changes and update URL
  const handleFilterChange = useCallback((key: string, value: string | undefined) => {
    const newFilters = { ...filters, [key]: value }
    setFilters(newFilters)

    // Update URL params
    const params = new URLSearchParams()
    Object.entries(newFilters).forEach(([k, v]) => {
      if (v) params.set(k, v)
    })
    router.push(`/tests?${params.toString()}`)
  }, [filters, router])

  const handleClearFilters = useCallback(() => {
    setFilters({
      environment_id: undefined,
      experiment_id: undefined,
      q: undefined,
    })
    router.push("/tests")
  }, [router])

  return (
    <MainLayout>
      <div className="max-w-7xl">
        {/* Page Header */}
        <div className="mb-6 flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold tracking-tight">Tests</h1>
            <p className="text-muted-foreground">
              Manage test executions and view results
            </p>
          </div>
          <NavigationButton href="/tests/add">
            <Plus className="mr-2 h-4 w-4" />
            Add Test
          </NavigationButton>
        </div>

        <div className="space-y-4">
          {/* Filters */}
          <TestsFilters
            filters={filters}
            onFilterChange={handleFilterChange}
            onClearFilters={handleClearFilters}
          />

          {/* Table or Loading/Error States */}
          {loading ? (
            <div className="space-y-3">
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-64 w-full" />
            </div>
          ) : error ? (
            <EmptyState
              icon={<FileText className="h-12 w-12" />}
              title="Failed to load tests"
              description={error.message}
              action={{
                label: "Retry",
                onClick: refetch,
              }}
            />
          ) : tests.length === 0 ? (
            <EmptyState
              icon={<FileText className="h-12 w-12" />}
              title="No tests found"
              description="No tests match your current filters. Try adjusting your search criteria."
              action={
                Object.values(filters).some((v) => v)
                  ? {
                      label: "Clear Filters",
                      onClick: handleClearFilters,
                    }
                  : undefined
              }
            />
          ) : (
            <>
              <TestsTable data={tests} sorting={sorting} onSortingChange={setSorting} />
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

export default function TestsPage() {
  return (
    <Suspense fallback={<div className="flex items-center justify-center h-screen"><div>Loading...</div></div>}>
      <TestsPageContent />
    </Suspense>
  )
}
