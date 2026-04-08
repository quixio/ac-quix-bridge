"use client"

/**
 * Custom React hook for fetching and managing Devices
 *
 * Uses Quix Auth Context for authentication with automatic token refresh on 401/403
 */

import { useState, useEffect, useMemo } from "react"
import { useDevicesApi } from "./use-api"
import type { Device, DeviceQuery, DeviceJournalEntry } from "@/types/device"
import type { PaginatedResponse, PageSize} from "@/types/pagination"

export function useDevices(initialQuery?: DeviceQuery) {
  const devicesApi = useDevicesApi() // Clean! Auth auto-injected
  const [devices, setDevices] = useState<Device[]>([])
  const [page, setPage] = useState(initialQuery?.page || 1)
  const [pageSize, setPageSize] = useState<PageSize>((initialQuery?.page_size as PageSize) || 10)
  const [total, setTotal] = useState(0)
  const [totalPages, setTotalPages] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<Error | null>(null)
  const [refetchTrigger, setRefetchTrigger] = useState(0)

  // Build query with pagination
  const query = useMemo(() => ({
    ...initialQuery,
    page,
    page_size: pageSize,
  }), [initialQuery, page, pageSize])

  // Create stable query key by serializing only defined properties
  const queryKey = useMemo(() => {
    const definedProps: Record<string, any> = {};
    Object.entries(query).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== "") {
        definedProps[key] = value;
      }
    });

    return JSON.stringify(definedProps);
  }, [query])

  useEffect(() => {
    let cancelled = false

    async function fetchDevices() {
      try {
        setLoading(true)
        setError(null)
        const data: PaginatedResponse<Device> = await devicesApi.list(query) // Clean! No token params needed

        if (!cancelled) {
          setDevices(data.items)
          setTotal(data.total)
          setTotalPages(data.total_pages)
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err : new Error("Failed to fetch Devices"))
        }
      } finally {
        if (!cancelled) {
          setLoading(false)
        }
      }
    }

    fetchDevices()

    return () => {
      cancelled = true
    }
  }, [queryKey, refetchTrigger, devicesApi])

  const refetch = () => {
    setRefetchTrigger((prev) => prev + 1)
  }

  const goToPage = (newPage: number) => {
    setPage(newPage)
  }

  const changePageSize = (newPageSize: PageSize) => {
    setPageSize(newPageSize)
    setPage(1) // Reset to first page when changing page size
  }

  return {
    devices,
    loading,
    error,
    refetch,
    // Pagination
    page,
    pageSize,
    total,
    totalPages,
    goToPage,
    changePageSize,
  }
}

export function useDevice(deviceId: string | null) {
  const devicesApi = useDevicesApi()
  const [device, setDevice] = useState<Device | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<Error | null>(null)
  const [refetchTrigger, setRefetchTrigger] = useState(0)

  useEffect(() => {
    if (!deviceId) {
      setDevice(null)
      setLoading(false)
      return
    }

    let cancelled = false

    async function fetchDevice() {
      try {
        setLoading(true)
        setError(null)
        const data = await devicesApi.get(deviceId!)

        if (!cancelled) {
          setDevice(data)
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err : new Error("Failed to fetch Device"))
        }
      } finally {
        if (!cancelled) {
          setLoading(false)
        }
      }
    }

    fetchDevice()

    return () => {
      cancelled = true
    }
  }, [deviceId, refetchTrigger, devicesApi])

  const refetch = () => {
    setRefetchTrigger((prev) => prev + 1)
  }

  return { device, loading, error, refetch }
}

export function useDeviceJournal(deviceId: string | null) {
  const devicesApi = useDevicesApi()
  const [journal, setJournal] = useState<DeviceJournalEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<Error | null>(null)
  const [refetchTrigger, setRefetchTrigger] = useState(0)

  useEffect(() => {
    if (!deviceId) {
      setJournal([])
      setLoading(false)
      return
    }

    let cancelled = false

    async function fetchJournal() {
      try {
        setLoading(true)
        setError(null)
        const data = await devicesApi.getJournal(deviceId!)

        if (!cancelled) {
          setJournal(data)
        }
      } catch (err) {
        if (!cancelled) {
          setError(
            err instanceof Error ? err : new Error("Failed to fetch journal")
          )
        }
      } finally {
        if (!cancelled) {
          setLoading(false)
        }
      }
    }

    fetchJournal()

    return () => {
      cancelled = true
    }
  }, [deviceId, refetchTrigger, devicesApi])

  const refetch = () => {
    setRefetchTrigger((prev) => prev + 1)
  }

  return { journal, loading, error, refetch }
}
