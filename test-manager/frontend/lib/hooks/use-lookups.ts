/**
 * Custom hooks for fetching lookup data with caching
 */

import { useState, useEffect } from "react"
import { useLookupsApi } from "./use-api"
import type {
  SampleType,
  Location,
  ProductCategory,
  Product,
} from "@/types/device"

// Cache expiry time: 24 hours
const CACHE_EXPIRY_MS = 24 * 60 * 60 * 1000

// Helper functions for localStorage with error handling
const loadFromLocalStorage = <T,>(key: string): T | null => {
  if (typeof window === "undefined") return null
  try {
    const item = localStorage.getItem(key)
    if (!item) return null
    const { data, timestamp } = JSON.parse(item)
    // Check if cache is expired
    if (Date.now() - timestamp > CACHE_EXPIRY_MS) {
      localStorage.removeItem(key)
      return null
    }
    return data as T
  } catch (error) {
    console.warn(`Failed to load ${key} from localStorage:`, error)
    return null
  }
}

const saveToLocalStorage = <T,>(key: string, data: T): void => {
  if (typeof window === "undefined") return
  try {
    localStorage.setItem(
      key,
      JSON.stringify({ data, timestamp: Date.now() })
    )
  } catch (error) {
    console.warn(`Failed to save ${key} to localStorage:`, error)
  }
}

// Simple in-memory cache for lookup data
const cache: {
  sampleTypes?: SampleType[]
  locations?: Location[]
  productCategories?: ProductCategory[]
  products?: Map<string, Product[]> // key: "manufacturer|category"
} = {}

/**
 * Hook to fetch sample types with caching (memory + localStorage)
 */
export function useSampleTypes() {
  const lookupsApi = useLookupsApi()
  // Initialize from cache or localStorage
  const initialData = cache.sampleTypes || loadFromLocalStorage<SampleType[]>("lookup_sampleTypes") || []
  const [sampleTypes, setSampleTypes] = useState<SampleType[]>(initialData)
  const [loading, setLoading] = useState(!cache.sampleTypes && !initialData.length)
  const [error, setError] = useState<Error | null>(null)

  useEffect(() => {
    // If memory cache exists, use it
    if (cache.sampleTypes) {
      setSampleTypes(cache.sampleTypes)
      setLoading(false)
      return
    }

    // If localStorage has data, use it and update memory cache
    const cachedData = loadFromLocalStorage<SampleType[]>("lookup_sampleTypes")
    if (cachedData && cachedData.length > 0) {
      cache.sampleTypes = cachedData
      setSampleTypes(cachedData)
      setLoading(false)
      return
    }

    // Fetch from API
    const fetchData = async () => {
      try {
        setLoading(true)
        const data = await lookupsApi.getSampleTypes()
        cache.sampleTypes = data
        saveToLocalStorage("lookup_sampleTypes", data)
        setSampleTypes(data)
        setError(null)
      } catch (err) {
        setError(err as Error)
      } finally {
        setLoading(false)
      }
    }

    fetchData()
  }, [])

  return { sampleTypes, loading, error }
}

/**
 * Hook to fetch locations with caching
 */
export function useLocations() {
  const lookupsApi = useLookupsApi()
  const [locations, setLocations] = useState<Location[]>(cache.locations || [])
  const [loading, setLoading] = useState(!cache.locations)
  const [error, setError] = useState<Error | null>(null)

  useEffect(() => {
    if (cache.locations) {
      setLocations(cache.locations)
      setLoading(false)
      return
    }

    const fetchData = async () => {
      try {
        setLoading(true)
        const data = await lookupsApi.getLocations()
        cache.locations = data
        setLocations(data)
        setError(null)
      } catch (err) {
        setError(err as Error)
      } finally {
        setLoading(false)
      }
    }

    fetchData()
  }, [])

  return { locations, loading, error }
}

/**
 * Hook to fetch product categories with caching
 */
export function useProductCategories() {
  const lookupsApi = useLookupsApi()
  const [categories, setCategories] = useState<ProductCategory[]>(cache.productCategories || [])
  const [loading, setLoading] = useState(!cache.productCategories)
  const [error, setError] = useState<Error | null>(null)

  useEffect(() => {
    if (cache.productCategories) {
      setCategories(cache.productCategories)
      setLoading(false)
      return
    }

    const fetchData = async () => {
      try {
        setLoading(true)
        const data = await lookupsApi.getProductCategories()
        cache.productCategories = data
        setCategories(data)
        setError(null)
      } catch (err) {
        setError(err as Error)
      } finally {
        setLoading(false)
      }
    }

    fetchData()
  }, [])

  return { categories, loading, error }
}

/**
 * Hook to fetch products with optional filtering by manufacturer and category
 * Caches results by filter combination
 */
export function useProducts(manufacturer?: string, productCategory?: string) {
  const lookupsApi = useLookupsApi()
  const cacheKey = `${manufacturer || ""}|${productCategory || ""}`

  if (!cache.products) {
    cache.products = new Map()
  }

  const [products, setProducts] = useState<Product[]>(cache.products.get(cacheKey) || [])
  const [loading, setLoading] = useState(!cache.products.has(cacheKey))
  const [error, setError] = useState<Error | null>(null)

  useEffect(() => {
    const cachedData = cache.products?.get(cacheKey)
    if (cachedData) {
      setProducts(cachedData)
      setLoading(false)
      return
    }

    const fetchData = async () => {
      try {
        setLoading(true)
        const data = await lookupsApi.getProducts({
          manufacturer,
          product_category: productCategory,
        })
        cache.products?.set(cacheKey, data)
        setProducts(data)
        setError(null)
      } catch (err) {
        setError(err as Error)
      } finally {
        setLoading(false)
      }
    }

    fetchData()
  }, [manufacturer, productCategory, cacheKey])

  return { products, loading, error }
}

/**
 * Helper to get unique manufacturers from products
 */
export function useManufacturers() {
  const { products, loading, error } = useProducts()

  const manufacturers = Array.from(
    new Set(products.map((p) => p.manufacturer))
  ).sort()

  return { manufacturers, loading, error }
}

/**
 * Clear all cached lookup data (useful for testing or forced refresh)
 */
export function clearLookupCache() {
  cache.sampleTypes = undefined
  cache.locations = undefined
  cache.productCategories = undefined
  cache.products = undefined
}
