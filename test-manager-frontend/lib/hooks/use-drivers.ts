"use client"

import { useState, useEffect, useMemo } from "react"
import { useDriversApi } from "./use-api"
import type { Driver, DriverQuery } from "@/types/driver"
import type { PaginatedResponse, PageSize } from "@/types/pagination"

export function useDrivers(initialQuery?: DriverQuery) {
  const driversApi = useDriversApi()
  const [drivers, setDrivers] = useState<Driver[]>([])
  const [page, setPage] = useState(initialQuery?.page || 1)
  const [pageSize, setPageSize] = useState<PageSize>((initialQuery?.page_size as PageSize) || 10)
  const [total, setTotal] = useState(0)
  const [totalPages, setTotalPages] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<Error | null>(null)
  const [refetchTrigger, setRefetchTrigger] = useState(0)

  const query = useMemo(() => ({
    ...initialQuery,
    page,
    page_size: pageSize,
  }), [initialQuery, page, pageSize])

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

    async function fetchDrivers() {
      try {
        setLoading(true)
        setError(null)
        const data: PaginatedResponse<Driver> = await driversApi.list(query)

        if (!cancelled) {
          setDrivers(data.items)
          setTotal(data.total)
          setTotalPages(data.total_pages)
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err : new Error("Failed to fetch drivers"))
        }
      } finally {
        if (!cancelled) {
          setLoading(false)
        }
      }
    }

    fetchDrivers()
    return () => { cancelled = true }
  }, [queryKey, refetchTrigger, driversApi])

  const refetch = () => setRefetchTrigger((prev) => prev + 1)
  const goToPage = (newPage: number) => setPage(newPage)
  const changePageSize = (newPageSize: PageSize) => {
    setPageSize(newPageSize)
    setPage(1)
  }

  return { drivers, loading, error, refetch, page, pageSize, total, totalPages, goToPage, changePageSize }
}

export function useDriver(driverId: string | null) {
  const driversApi = useDriversApi()
  const [driver, setDriver] = useState<Driver | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<Error | null>(null)
  const [refetchTrigger, setRefetchTrigger] = useState(0)

  useEffect(() => {
    if (!driverId) {
      setDriver(null)
      setLoading(false)
      return
    }

    let cancelled = false

    async function fetchDriver() {
      try {
        setLoading(true)
        setError(null)
        const data = await driversApi.get(driverId!)
        if (!cancelled) setDriver(data)
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err : new Error("Failed to fetch driver"))
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    fetchDriver()
    return () => { cancelled = true }
  }, [driverId, refetchTrigger, driversApi])

  const refetch = () => setRefetchTrigger((prev) => prev + 1)
  return { driver, loading, error, refetch }
}
