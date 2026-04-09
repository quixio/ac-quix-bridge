"use client"

/**
 * Stub for old lookup hooks — these are no longer used after Device simplification.
 * Kept temporarily so test-related components compile.
 * Will be removed when Tests tab is adapted.
 */

export function useSampleTypes() {
  return { sampleTypes: [], loading: false }
}

export function useLocations() {
  return { locations: [], loading: false }
}

export function useProductCategories() {
  return { productCategories: [], loading: false }
}

export function useProducts(_manufacturer?: string, _category?: string) {
  return { products: [], loading: false }
}
