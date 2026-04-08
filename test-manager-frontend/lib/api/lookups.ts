/**
 * API client for lookup tables
 */

import { apiGet } from "./client"
import type {
  SampleType,
  Location,
  Product,
  ProductCategory,
} from "@/types/device"

export const lookupsApi = {
  /**
   * Get all sample types
   */
  getSampleTypes: (
    token?: string | null,
    refreshToken?: () => Promise<string | null>
  ) => {
    return apiGet<SampleType[]>("/lookups/sample-types", undefined, token, refreshToken)
  },

  /**
   * Get all locations
   */
  getLocations: (
    token?: string | null,
    refreshToken?: () => Promise<string | null>
  ) => {
    return apiGet<Location[]>("/lookups/locations", undefined, token, refreshToken)
  },

  /**
   * Get all products with optional filtering
   */
  getProducts: (
    params?: {
      manufacturer?: string
      product_category?: string
    },
    token?: string | null,
    refreshToken?: () => Promise<string | null>
  ) => {
    return apiGet<Product[]>("/lookups/products", params, token, refreshToken)
  },

  /**
   * Get all product categories
   */
  getProductCategories: (
    token?: string | null,
    refreshToken?: () => Promise<string | null>
  ) => {
    return apiGet<ProductCategory[]>("/lookups/product-categories", undefined, token, refreshToken)
  },
}
