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
import { TestStatus } from "@/types/test"
import { X } from "lucide-react"
import { useDebouncedCallback } from "use-debounce"
import { useTestsApi } from "@/lib/hooks/use-api"

interface TestsFiltersProps {
  filters: {
    status?: TestStatus
    environment_id?: string
    campaign_id?: string
    q?: string
  }
  onFilterChange: (key: string, value: string | undefined) => void
  onClearFilters: () => void
}

export function TestsFilters({
  filters,
  onFilterChange,
  onClearFilters,
}: TestsFiltersProps) {
  // Local state for search input (updates immediately for UX)
  const [searchInput, setSearchInput] = useState(filters.q || "")

  // Sync local state when filter changes externally (e.g., Clear button)
  useEffect(() => {
    setSearchInput(filters.q || "")
  }, [filters.q])

  const hasActiveFilters =
    filters.status || filters.environment_id || filters.campaign_id || filters.q

  const testsApi = useTestsApi()
  const [campaignIds, setCampaignIds] = useState<string[]>([])
  const [environmentIds, setEnvironmentIds] = useState<string[]>([])

  // Fetch filter options
  useEffect(() => {
    const fetchFilters = async () => {
      try {
        const [campaignIdsData, environmentIdsData] = await Promise.all([
          testsApi.getCampaignIds(),
          testsApi.getEnvironmentIds(),
        ])
        setCampaignIds(campaignIdsData)
        setEnvironmentIds(environmentIdsData)
      } catch (error) {
        console.error("Failed to fetch filter options:", error)
      }
    }
    fetchFilters()
  }, [])

  // Prepare options for comboboxes
  const campaignIdOptions = campaignIds.map((id) => ({
    value: id,
    label: id,
  }))

  const environmentIdOptions = environmentIds.map((id) => ({
    value: id,
    label: id,
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
      <div className="flex-1 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        {/* Search */}
        <Input
          placeholder="Search tests..."
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
            <SelectItem value={TestStatus.DRAFT}>Draft</SelectItem>
            <SelectItem value={TestStatus.IN_PROGRESS}>In Progress</SelectItem>
            <SelectItem value={TestStatus.FINISHED}>Finished</SelectItem>
          </SelectContent>
        </Select>

        {/* Environment ID filter */}
        <Combobox
          options={environmentIdOptions}
          value={filters.environment_id}
          onValueChange={(value) => onFilterChange("environment_id", value)}
          placeholder="Environment ID"
          searchPlaceholder="Search or type Environment ID..."
          emptyText="No exact matches"
          className="w-full"
          allowCustomValue={true}
        />

        {/* Campaign filter */}
        <Combobox
          options={campaignIdOptions}
          value={filters.campaign_id}
          onValueChange={(value) => onFilterChange("campaign_id", value)}
          placeholder="Campaign ID"
          searchPlaceholder="Search or type campaign..."
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
