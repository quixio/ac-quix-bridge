"use client"

import { Suspense, useState, useCallback } from "react"
import { useSearchParams, useRouter } from "next/navigation"
import { SortingState } from "@tanstack/react-table"
import { MainLayout } from "@/components/layout/main-layout"
import { NavigationButton } from "@/components/ui/navigation-button"
import { DriversTable } from "@/components/drivers/drivers-table"
import { EmptyState } from "@/components/shared/empty-state"
import { Pagination } from "@/components/shared/pagination"
import { Skeleton } from "@/components/ui/skeleton"
import { Input } from "@/components/ui/input"
import { useDrivers } from "@/lib/hooks/use-drivers"
import { Plus, Users } from "lucide-react"

function DriversPageContent() {
  const router = useRouter()
  const searchParams = useSearchParams()

  const [filters, setFilters] = useState({
    q: searchParams.get("q") || undefined,
  })

  const [sorting, setSorting] = useState<SortingState>([{ id: "driver_id", desc: false }])

  const {
    drivers,
    loading,
    error,
    refetch,
    page,
    pageSize,
    total,
    totalPages,
    goToPage,
    changePageSize,
  } = useDrivers(filters)

  const handleSearch = useCallback((value: string) => {
    const q = value || undefined
    setFilters({ q })
    const params = new URLSearchParams()
    if (q) params.set("q", q)
    router.push(`/drivers?${params.toString()}`)
  }, [router])

  return (
    <MainLayout>
      <div className="max-w-7xl">
        <div className="mb-6 flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold tracking-tight">Drivers</h1>
            <p className="text-muted-foreground">
              Manage drivers for test sessions
            </p>
          </div>
          <NavigationButton href="/drivers/add">
            <Plus className="mr-2 h-4 w-4" />
            Add Driver
          </NavigationButton>
        </div>

        <div className="space-y-4">
          <Input
            placeholder="Search drivers..."
            defaultValue={filters.q}
            onChange={(e) => handleSearch(e.target.value)}
            className="max-w-sm"
          />

          {loading ? (
            <div className="space-y-3">
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-64 w-full" />
            </div>
          ) : error ? (
            <EmptyState
              icon={<Users className="h-12 w-12" />}
              title="Failed to load drivers"
              description={error.message}
              action={{ label: "Retry", onClick: refetch }}
            />
          ) : drivers.length === 0 ? (
            <EmptyState
              icon={<Users className="h-12 w-12" />}
              title="No drivers found"
              description={filters.q
                ? "No drivers match your search. Try adjusting your criteria."
                : "Get started by adding your first driver."
              }
              action={filters.q
                ? { label: "Clear Search", onClick: () => handleSearch("") }
                : { label: "Add Driver", onClick: () => router.push("/drivers/add") }
              }
            />
          ) : (
            <>
              <DriversTable data={drivers} sorting={sorting} onSortingChange={setSorting} />
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

export default function DriversPage() {
  return (
    <Suspense fallback={<div className="flex items-center justify-center h-screen"><div>Loading...</div></div>}>
      <DriversPageContent />
    </Suspense>
  )
}
