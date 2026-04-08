/**
 * Centralized date formatting hook that handles SSR properly
 *
 * This hook detects the browser's locale and provides consistent date formatting
 * functions throughout the application. It handles Next.js SSR by only formatting
 * dates on the client side, avoiding hydration mismatches.
 *
 * Includes intelligent caching to avoid redundant date formatting operations.
 */

"use client"

import { useState, useEffect, useCallback } from "react"

// Module-level cache shared across all hook instances
const dateFormatCache = new Map<string, string>()
const MAX_CACHE_SIZE = 1000

/**
 * Helper to get or set cached formatted date
 */
function getCachedOrFormat(
  cacheKey: string,
  formatFn: () => string
): string {
  // Check cache first
  const cached = dateFormatCache.get(cacheKey)
  if (cached) return cached

  // Format and cache
  const formatted = formatFn()

  // Maintain cache size limit (FIFO eviction)
  if (dateFormatCache.size >= MAX_CACHE_SIZE) {
    const firstKey = dateFormatCache.keys().next().value
    if (firstKey) dateFormatCache.delete(firstKey)
  }

  dateFormatCache.set(cacheKey, formatted)
  return formatted
}

export function useDateFormatter() {
  const [locale, setLocale] = useState<string | null>(null)

  // Detect browser locale on client mount
  useEffect(() => {
    const browserLocale = navigator.language || "de-DE"
    setLocale(browserLocale)

    // Debug logging
    console.log("Date Formatter Initialized:", {
      locale: browserLocale,
      languages: navigator.languages,
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    })
  }, [])

  /**
   * Format a date string to localized date format (e.g., DD/MM/YYYY)
   */
  const formatDate = useCallback(
    (dateString: string): string => {
      if (!locale) return "" // Return empty during SSR

      try {
        const cacheKey = `${locale}:${dateString}:date`
        return getCachedOrFormat(cacheKey, () => {
          const date = new Date(dateString)
          return date.toLocaleDateString(locale, {
            year: "numeric",
            month: "2-digit",
            day: "2-digit",
          })
        })
      } catch (error) {
        console.error("Error formatting date:", error)
        return dateString
      }
    },
    [locale]
  )

  /**
   * Format a date string to localized date and time format
   */
  const formatDateTime = useCallback(
    (dateString: string): string => {
      if (!locale) return ""

      try {
        const cacheKey = `${locale}:${dateString}:datetime`
        return getCachedOrFormat(cacheKey, () => {
          const date = new Date(dateString)
          return date.toLocaleString(locale, {
            year: "numeric",
            month: "2-digit",
            day: "2-digit",
            hour: "2-digit",
            minute: "2-digit",
          })
        })
      } catch (error) {
        console.error("Error formatting datetime:", error)
        return dateString
      }
    },
    [locale]
  )

  /**
   * Format a date string to localized date with short month name
   */
  const formatDateWithMonth = useCallback(
    (dateString: string): string => {
      if (!locale) return ""

      try {
        const cacheKey = `${locale}:${dateString}:month`
        return getCachedOrFormat(cacheKey, () => {
          const date = new Date(dateString)
          return date.toLocaleDateString(locale, {
            year: "numeric",
            month: "short",
            day: "numeric",
          })
        })
      } catch (error) {
        console.error("Error formatting date with month:", error)
        return dateString
      }
    },
    [locale]
  )

  /**
   * Get relative time string (e.g., "2 hours ago", "just now")
   * Falls back to formatted date for dates older than 30 days
   */
  const formatRelative = useCallback(
    (dateString: string): string => {
      if (!locale) return ""

      try {
        const date = new Date(dateString)
        const now = new Date()
        const diffMs = now.getTime() - date.getTime()
        const diffSec = Math.floor(diffMs / 1000)
        const diffMin = Math.floor(diffSec / 60)
        const diffHour = Math.floor(diffMin / 60)
        const diffDay = Math.floor(diffHour / 24)

        // Don't cache relative times - they change over time
        if (diffSec < 60) return "just now"
        if (diffMin < 60) return `${diffMin} minute${diffMin > 1 ? "s" : ""} ago`
        if (diffHour < 24) return `${diffHour} hour${diffHour > 1 ? "s" : ""} ago`
        if (diffDay < 30) return `${diffDay} day${diffDay > 1 ? "s" : ""} ago`

        // Cache formatted date fallback for old dates
        const cacheKey = `${locale}:${dateString}:relative-fallback`
        return getCachedOrFormat(cacheKey, () => {
          return date.toLocaleDateString(locale, {
            year: "numeric",
            month: "2-digit",
            day: "2-digit",
          })
        })
      } catch (error) {
        console.error("Error formatting relative time:", error)
        return dateString
      }
    },
    [locale]
  )

  return {
    locale,
    formatDate,
    formatDateTime,
    formatDateWithMonth,
    formatRelative,
    isReady: locale !== null,
  }
}
