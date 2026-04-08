/**
 * TypeScript types for pagination
 * Mirrors backend/api/models.py pagination models
 */

export const PAGE_SIZE_OPTIONS = [10, 20, 50, 100, 200] as const
export type PageSize = (typeof PAGE_SIZE_OPTIONS)[number]

export interface PaginationParams {
  page?: number // Page number (1-indexed, default: 1)
  page_size?: PageSize // Items per page (default: 50)
}

export interface PaginatedResponse<T> {
  items: T[]
  total: number // Total number of items across all pages
  page: number // Current page number
  page_size: number // Number of items per page
  total_pages: number // Total number of pages
}
