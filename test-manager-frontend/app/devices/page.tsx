"use client"

import { Suspense, useState, useCallback } from "react"
import { useSearchParams, useRouter } from "next/navigation"
import nextDynamic from "next/dynamic"
import { SortingState } from "@tanstack/react-table"
import { MainLayout } from "@/components/layout/main-layout"
import { NavigationButton } from "@/components/ui/navigation-button"
import { Skeleton } from "@/components/ui/skeleton"

// Lazy load DevicesTable to reduce initial bundle size
const DevicesTable = nextDynamic(() => import("@/components/devices/devices-table").then((mod) => ({ default: mod.DevicesTable })), {
  loading: () => <Skeleton className="h-96 w-full" />,
  ssr: false,
})
import { DevicesFilters } from "@/components/devices/devices-filters"
import { EmptyState } from "@/components/shared/empty-state"
import { Pagination } from "@/components/shared/pagination"
import { useDevices } from "@/lib/hooks/use-devices"
import { DeviceStatus } from "@/types/device"
import { Plus, Package } from "lucide-react"

// Inner component that uses useSearchParams
function DevicesPageContent() {
  const router = useRouter()
  const searchParams = useSearchParams()

  // Get filters from URL
  const [filters, setFilters] = useState({
    status: searchParams.get("status") as DeviceStatus | undefined,
    location: searchParams.get("location") || undefined,
    product_category: searchParams.get("product_category") || undefined,
    product_name: searchParams.get("product_name") || undefined,
    q: searchParams.get("q") || undefined,
  })

  const [sorting, setSorting] = useState<SortingState>([{ id: "created_at", desc: true }])

  // Fetch devices with filters and pagination
  const {
    devices,
    loading,
    error,
    refetch,
    page,
    pageSize,
    total,
    totalPages,
    goToPage,
    changePageSize,
  } = useDevices(filters)

  // Handle filter changes and update URL
  const handleFilterChange = useCallback((key: string, value: string | undefined) => {
    const newFilters = { ...filters, [key]: value }
    setFilters(newFilters)

    // Update URL params
    const params = new URLSearchParams()
    Object.entries(newFilters).forEach(([k, v]) => {
      if (v) params.set(k, v)
    })
    router.push(`/devices?${params.toString()}`)
  }, [filters, router])

  const handleClearFilters = useCallback(() => {
    setFilters({
      status: undefined,
      location: undefined,
      product_category: undefined,
      product_name: undefined,
      q: undefined,
    })
    router.push("/devices")
  }, [router])

  return (
    <MainLayout>
      <div className="max-w-7xl">
        {/* Page Header */}
        <div className="mb-6 flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold tracking-tight">Devices</h1>
            <p className="text-muted-foreground">
              Manage devices under test and view their history
            </p>
          </div>
          <NavigationButton href="/devices/add">
            <Plus className="mr-2 h-4 w-4" />
            Add Device
          </NavigationButton>
        </div>

        <div className="space-y-4">

        {/* Filters */}
        <DevicesFilters
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
            icon={<Package className="h-12 w-12" />}
            title="Failed to load devices"
            description={error.message}
            action={{
              label: "Retry",
              onClick: refetch,
            }}
          />
        ) : devices.length === 0 ? (
          <EmptyState
            icon={<Package className="h-12 w-12" />}
            title="No devices found"
            description="No devices match your current filters. Try adjusting your search criteria."
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
            <DevicesTable data={devices} sorting={sorting} onSortingChange={setSorting} />
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

export default function DevicesPage() {
  return (
    <Suspense fallback={<div className="flex items-center justify-center h-screen"><div>Loading...</div></div>}>
      <DevicesPageContent />
    </Suspense>
  )
}
