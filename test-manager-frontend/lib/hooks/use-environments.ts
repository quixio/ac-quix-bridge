"use client"

import { useState, useEffect, useMemo } from "react"
import { useEnvironmentsApi } from "./use-api"
import type { Environment, EnvironmentQuery } from "@/types/environment"
import type { PaginatedResponse, PageSize } from "@/types/pagination"

export function useEnvironments(initialQuery?: EnvironmentQuery) {
  const environmentsApi = useEnvironmentsApi()
  const [environments, setEnvironments] = useState<Environment[]>([])
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

    async function fetchEnvironments() {
      try {
        setLoading(true)
        setError(null)
        const data: PaginatedResponse<Environment> = await environmentsApi.list(query)

        if (!cancelled) {
          setEnvironments(data.items)
          setTotal(data.total)
          setTotalPages(data.total_pages)
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err : new Error("Failed to fetch environments"))
        }
      } finally {
        if (!cancelled) {
          setLoading(false)
        }
      }
    }

    fetchEnvironments()
    return () => { cancelled = true }
  }, [queryKey, refetchTrigger, environmentsApi])

  const refetch = () => setRefetchTrigger((prev) => prev + 1)
  const goToPage = (newPage: number) => setPage(newPage)
  const changePageSize = (newPageSize: PageSize) => {
    setPageSize(newPageSize)
    setPage(1)
  }

  return { environments, loading, error, refetch, page, pageSize, total, totalPages, goToPage, changePageSize }
}

export function useEnvironment(environmentId: string | null) {
  const environmentsApi = useEnvironmentsApi()
  const [environment, setEnvironment] = useState<Environment | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<Error | null>(null)
  const [refetchTrigger, setRefetchTrigger] = useState(0)

  useEffect(() => {
    if (!environmentId) {
      setEnvironment(null)
      setLoading(false)
      return
    }

    let cancelled = false

    async function fetchEnvironment() {
      try {
        setLoading(true)
        setError(null)
        const data = await environmentsApi.get(environmentId!)
        if (!cancelled) setEnvironment(data)
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err : new Error("Failed to fetch environment"))
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    fetchEnvironment()
    return () => { cancelled = true }
  }, [environmentId, refetchTrigger, environmentsApi])

  const refetch = () => setRefetchTrigger((prev) => prev + 1)
  return { environment, loading, error, refetch }
}
