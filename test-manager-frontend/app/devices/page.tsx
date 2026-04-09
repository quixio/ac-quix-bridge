"use client"

import { Suspense, useState, useCallback } from "react"
import { useSearchParams, useRouter } from "next/navigation"
import { SortingState } from "@tanstack/react-table"
import { MainLayout } from "@/components/layout/main-layout"
import { NavigationButton } from "@/components/ui/navigation-button"
import { DevicesTable } from "@/components/devices/devices-table"
import { EmptyState } from "@/components/shared/empty-state"
import { Pagination } from "@/components/shared/pagination"
import { Skeleton } from "@/components/ui/skeleton"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { useDevices } from "@/lib/hooks/use-devices"
import { DeviceCategory, DeviceCategoryLabels, DeviceStatus } from "@/types/device"
import { Plus, Box } from "lucide-react"

function DevicesPageContent() {
  const router = useRouter()
  const searchParams = useSearchParams()

  const [filters, setFilters] = useState({
    category: searchParams.get("category") as DeviceCategory | undefined,
    status: searchParams.get("status") as DeviceStatus | undefined,
    q: searchParams.get("q") || undefined,
  })

  const [sorting, setSorting] = useState<SortingState>([{ id: "device_id", desc: false }])

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

  const updateFilters = useCallback((newFilters: typeof filters) => {
    setFilters(newFilters)
    const params = new URLSearchParams()
    Object.entries(newFilters).forEach(([k, v]) => {
      if (v) params.set(k, v)
    })
    router.push(`/devices?${params.toString()}`)
  }, [router])

  const handleSearch = useCallback((value: string) => {
    updateFilters({ ...filters, q: value || undefined })
  }, [filters, updateFilters])

  const handleCategoryChange = useCallback((value: string) => {
    updateFilters({ ...filters, category: value === "all" ? undefined : value as DeviceCategory })
  }, [filters, updateFilters])

  const handleStatusChange = useCallback((value: string) => {
    updateFilters({ ...filters, status: value === "all" ? undefined : value as DeviceStatus })
  }, [filters, updateFilters])

  const handleClearFilters = useCallback(() => {
    updateFilters({ category: undefined, status: undefined, q: undefined })
  }, [updateFilters])

  return (
    <MainLayout>
      <div className="max-w-7xl">
        <div className="mb-6 flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold tracking-tight">Devices</h1>
            <p className="text-muted-foreground">
              Manage PCs and test rigs
            </p>
          </div>
          <NavigationButton href="/devices/add">
            <Plus className="mr-2 h-4 w-4" />
            Add Device
          </NavigationButton>
        </div>

        <div className="space-y-4">
          <div className="flex gap-4">
            <Input
              placeholder="Search devices..."
              defaultValue={filters.q}
              onChange={(e) => handleSearch(e.target.value)}
              className="max-w-sm"
            />
            <Select value={filters.category || "all"} onValueChange={handleCategoryChange}>
              <SelectTrigger className="w-[180px]">
                <SelectValue placeholder="All Categories" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All Categories</SelectItem>
                <SelectItem value={DeviceCategory.PC}>{DeviceCategoryLabels[DeviceCategory.PC]}</SelectItem>
                <SelectItem value={DeviceCategory.TEST_RIG}>{DeviceCategoryLabels[DeviceCategory.TEST_RIG]}</SelectItem>
              </SelectContent>
            </Select>
            <Select value={filters.status || "all"} onValueChange={handleStatusChange}>
              <SelectTrigger className="w-[180px]">
                <SelectValue placeholder="All Statuses" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All Statuses</SelectItem>
                <SelectItem value={DeviceStatus.ACTIVE}>Active</SelectItem>
                <SelectItem value={DeviceStatus.INACTIVE}>Inactive</SelectItem>
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
              icon={<Box className="h-12 w-12" />}
              title="Failed to load devices"
              description={error.message}
              action={{ label: "Retry", onClick: refetch }}
            />
          ) : devices.length === 0 ? (
            <EmptyState
              icon={<Box className="h-12 w-12" />}
              title="No devices found"
              description={filters.q || filters.category || filters.status
                ? "No devices match your filters. Try adjusting your criteria."
                : "Get started by adding your first device."
              }
              action={filters.q || filters.category || filters.status
                ? { label: "Clear Filters", onClick: handleClearFilters }
                : { label: "Add Device", onClick: () => router.push("/devices/add") }
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
