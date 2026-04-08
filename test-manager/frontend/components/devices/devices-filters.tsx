"use client"

import { useState, useEffect } from "react"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Combobox } from "@/components/ui/combobox"
import { DeviceStatus } from "@/types/device"
import { X } from "lucide-react"
import { useDebouncedCallback } from "use-debounce"
import { useLocations, useProductCategories, useProducts } from "@/lib/hooks/use-lookups"

interface DevicesFiltersProps {
  filters: {
    status?: DeviceStatus
    location?: string
    product_category?: string
    product_name?: string
    q?: string
  }
  onFilterChange: (key: string, value: string | undefined) => void
  onClearFilters: () => void
}

export function DevicesFilters({
  filters,
  onFilterChange,
  onClearFilters,
}: DevicesFiltersProps) {
  // Local state for search input (updates immediately for UX)
  const [searchInput, setSearchInput] = useState(filters.q || "")

  // Sync local state when filter changes externally (e.g., Clear button)
  useEffect(() => {
    setSearchInput(filters.q || "")
  }, [filters.q])

  const hasActiveFilters =
    filters.status ||
    filters.location ||
    filters.product_category ||
    filters.product_name ||
    filters.q

  // Fetch lookup data
  const { locations } = useLocations()
  const { categories } = useProductCategories()
  const { products } = useProducts()

  // Prepare options for comboboxes
  const locationOptions = locations.map((loc) => ({
    value: loc.location,
    label: loc.location,
  }))

  const categoryOptions = categories.map((cat) => ({
    value: cat._id,
    label: cat.name,
  }))

  const productOptions = Array.from(
    new Set(products.map((p) => p.product_name))
  )
    .sort()
    .map((name) => ({
      value: name,
      label: name,
    }))

  // Debounced filter change (300ms delay)
  const debouncedFilterChange = useDebouncedCallback(
    (key: string, value: string | undefined) => {
      onFilterChange(key, value)
    },
    300
  )

  return (
    <div className="flex flex-col gap-4 sm:flex-row sm:items-center">
      <div className="flex-1 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-4">
        {/* Search */}
        <Input
          placeholder="Search Devices..."
          value={searchInput}
          onChange={(e) => {
            const value = e.target.value
            setSearchInput(value)
            debouncedFilterChange("q", value || undefined)
          }}
          className="w-full"
        />

        {/* Status filter */}
        <Select
          value={filters.status || "all"}
          onValueChange={(value) =>
            onFilterChange("status", value === "all" ? undefined : value)
          }
        >
          <SelectTrigger className="w-full">
            <SelectValue placeholder="Status" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All Statuses</SelectItem>
            <SelectItem value={DeviceStatus.CREATED}>Created</SelectItem>
            <SelectItem value={DeviceStatus.SETUP}>Setup</SelectItem>
            <SelectItem value={DeviceStatus.STORED}>Stored</SelectItem>
            <SelectItem value={DeviceStatus.SCRAPPED}>Scrapped</SelectItem>
          </SelectContent>
        </Select>

        {/* Location filter */}
        <Combobox
          options={locationOptions}
          value={filters.location}
          onValueChange={(value) => onFilterChange("location", value)}
          placeholder="Location"
          searchPlaceholder="Search or type location..."
          emptyText="No exact matches"
          className="w-full"
          allowCustomValue={true}
        />

        {/* Product category filter */}
        <Combobox
          options={categoryOptions}
          value={filters.product_category}
          onValueChange={(value) => onFilterChange("product_category", value)}
          placeholder="Product Category"
          searchPlaceholder="Search or type category..."
          emptyText="No exact matches"
          className="w-full"
          allowCustomValue={true}
        />

        {/* Product name filter */}
        <Combobox
          options={productOptions}
          value={filters.product_name}
          onValueChange={(value) => onFilterChange("product_name", value)}
          placeholder="Product Name"
          searchPlaceholder="Search or type product..."
          emptyText="No exact matches"
          className="w-full"
          allowCustomValue={true}
        />
      </div>

      {/* Clear filters button */}
      {hasActiveFilters && (
        <Button
          variant="outline"
          size="sm"
          onClick={onClearFilters}
          className="shrink-0"
        >
          <X className="mr-2 h-4 w-4" />
          Clear
        </Button>
      )}
    </div>
  )
}
