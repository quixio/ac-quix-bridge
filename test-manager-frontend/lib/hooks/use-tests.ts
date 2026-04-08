"use client"

/**
 * Custom React hook for fetching and managing tests
 */

import { useState, useEffect, useMemo } from "react"
import { useTestsApi } from "./use-api"
import type { Test, TestQuery, TestFullData } from "@/types/test"
import type { PaginatedResponse, PageSize } from "@/types/pagination"

export function useTests(initialQuery?: TestQuery) {
  const testsApi = useTestsApi()
  const [tests, setTests] = useState<Test[]>([])
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

    async function fetchTests() {
      try {
        setLoading(true)
        setError(null)
        const data: PaginatedResponse<Test> = await testsApi.list(query)

        if (!cancelled) {
          setTests(data.items)
          setTotal(data.total)
          setTotalPages(data.total_pages)
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err : new Error("Failed to fetch tests"))
        }
      } finally {
        if (!cancelled) {
          setLoading(false)
        }
      }
    }

    fetchTests()

    return () => {
      cancelled = true
    }
  }, [queryKey, refetchTrigger])

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
    tests,
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

export function useTest(testId: string | null) {
  const testsApi = useTestsApi()
  const [test, setTest] = useState<Test | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<Error | null>(null)
  const [refetchTrigger, setRefetchTrigger] = useState(0)

  useEffect(() => {
    if (!testId) {
      setTest(null)
      setLoading(false)
      return
    }

    let cancelled = false

    async function fetchTest() {
      try {
        setLoading(true)
        setError(null)
        const data = await testsApi.get(testId!)

        if (!cancelled) {
          setTest(data)
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err : new Error("Failed to fetch test"))
        }
      } finally {
        if (!cancelled) {
          setLoading(false)
        }
      }
    }

    fetchTest()

    return () => {
      cancelled = true
    }
  }, [testId, refetchTrigger])

  const refetch = () => {
    setRefetchTrigger((prev) => prev + 1)
  }

  return { test, loading, error, refetch }
}

export function useTestFull(testId: string | null) {
  const testsApi = useTestsApi()
  const [testFull, setTestFull] = useState<TestFullData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<Error | null>(null)
  const [refetchTrigger, setRefetchTrigger] = useState(0)

  useEffect(() => {
    if (!testId) {
      setTestFull(null)
      setLoading(false)
      return
    }

    let cancelled = false

    async function fetchTestFull() {
      try {
        setLoading(true)
        setError(null)
        const data = await testsApi.getFull(testId!)

        if (!cancelled) {
          setTestFull(data)
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err : new Error("Failed to fetch test"))
        }
      } finally {
        if (!cancelled) {
          setLoading(false)
        }
      }
    }

    fetchTestFull()

    return () => {
      cancelled = true
    }
  }, [testId, refetchTrigger])

  const refetch = () => {
    setRefetchTrigger((prev) => prev + 1)
  }

  return { testFull, loading, error, refetch }
}
