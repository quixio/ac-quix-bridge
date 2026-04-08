"use client"

import { useState, useEffect } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Combobox } from "@/components/ui/combobox"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Checkbox } from "@/components/ui/checkbox"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import { useDevicesApi } from "@/lib/hooks/use-api"
import { useLocations, useProductCategories, useProducts, useSampleTypes } from "@/lib/hooks/use-lookups"
import { DeviceStatus, DeviceStatusLabels } from "@/types/device"
import type { Device, DeviceQuery } from "@/types/device"
import type { DeviceReference } from "@/types/test"
import { X, Search } from "lucide-react"
import { DeviceStatusBadge } from "@/components/devices/device-status-badge"

// Simple module-level cache for Device list to avoid refetching on every dialog open
let cachedDevices: Device[] | null = null
let cacheTimestamp: number = 0
const CACHE_DURATION_MS = 5 * 60 * 1000 // 5 minutes

interface DevicePickerDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  selectedDevices: DeviceReference[]
  onConfirm: (devices: DeviceReference[]) => void
}

export function DevicePickerDialog({
  open,
  onOpenChange,
  selectedDevices,
  onConfirm,
}: DevicePickerDialogProps) {
  const devicesApi = useDevicesApi()

  // Fetch lookup data
  const { locations } = useLocations()
  const { categories } = useProductCategories()
  const { products } = useProducts()
  const { sampleTypes } = useSampleTypes()

  // Filter state
  const [filters, setFilters] = useState<DeviceQuery>({})

  // Devices data
  const [devices, setDevices] = useState<Device[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Selection state
  const [selected, setSelected] = useState<Set<string>>(new Set())

  // Initialize selected Devices when dialog opens
  useEffect(() => {
    if (open) {
      const selectedIds = new Set(selectedDevices.map((d) => d.device_id))
      setSelected(selectedIds)
    }
  }, [open, selectedDevices])

  // Fetch Devices when filters change
  useEffect(() => {
    if (!open) return

    // Check if we have valid cached data (only when no filters applied)
    const hasFilters = Object.keys(filters).some(key => filters[key as keyof DeviceQuery])
    const isCacheValid = cachedDevices && !hasFilters && (Date.now() - cacheTimestamp < CACHE_DURATION_MS)

    if (isCacheValid) {
      setDevices(cachedDevices!)
      setLoading(false)
      return
    }

    const fetchDevices = async () => {
      setLoading(true)
      setError(null)
      try {
        const result = await devicesApi.list({ ...filters, page_size: 100 })
        setDevices(result.items)

        // Cache only when no filters applied
        if (!hasFilters) {
          cachedDevices = result.items
          cacheTimestamp = Date.now()
        }
      } catch (err) {
        console.error("Failed to fetch Devices:", err)
        setError(err instanceof Error ? err.message : "Failed to fetch Devices")
      } finally {
        setLoading(false)
      }
    }

    const debounce = setTimeout(fetchDevices, 300)
    return () => clearTimeout(debounce)
  }, [filters, open])

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

  const manufacturerOptions = Array.from(
    new Set(products.map((p) => p.manufacturer))
  )
    .sort()
    .map((manufacturer) => ({
      value: manufacturer,
      label: manufacturer,
    }))

  const sampleTypeOptions = sampleTypes.map((st) => ({
    value: st.sample_type,
    label: st.sample_type,
  }))

  const handleFilterChange = (key: keyof DeviceQuery, value: string | undefined) => {
    setFilters((prev) => ({
      ...prev,
      [key]: value || undefined,
    }))
  }

  const handleClearFilters = () => {
    setFilters({})
  }

  const handleToggleSelect = (deviceId: string) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(deviceId)) {
        next.delete(deviceId)
      } else {
        next.add(deviceId)
      }
      return next
    })
  }

  const handleToggleAll = () => {
    if (selected.size === devices.length && devices.length > 0) {
      setSelected(new Set())
    } else {
      setSelected(new Set(devices.map((d) => d.device_id)))
    }
  }

  const handleConfirm = () => {
    const selectedReferences: DeviceReference[] = Array.from(selected).map(
      (deviceId) => ({
        device_id: deviceId,
        device_version: null,
      })
    )
    onConfirm(selectedReferences)
    onOpenChange(false)
  }

  const handleCancel = () => {
    // Reset selection to original
    const selectedIds = new Set(selectedDevices.map((d) => d.device_id))
    setSelected(selectedIds)
    onOpenChange(false)
  }

  const hasActiveFilters =
    filters.q ||
    filters.status ||
    filters.location ||
    filters.product_category ||
    filters.product_name ||
    filters.manufacturer ||
    filters.sample_type

  const allSelected = devices.length > 0 && selected.size === devices.length

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-5xl h-[90vh] flex flex-col">
        <DialogHeader>
          <DialogTitle>Select Devices</DialogTitle>
          <DialogDescription>
            Search and filter to find Devices, then select multiple to add to the test.
          </DialogDescription>
        </DialogHeader>

        {/* Filters Section */}
        <div className="space-y-3 py-4">
          {/* Search Row */}
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
            <Input
              placeholder="Search Devices (ID, sample, manufacturer, product, location)..."
              value={filters.q || ""}
              onChange={(e) => handleFilterChange("q", e.target.value || undefined)}
              className="pl-10"
            />
          </div>

          {/* Filter Grid */}
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
            {/* Status Filter */}
            <Select
              value={filters.status || "all"}
              onValueChange={(value) =>
                handleFilterChange("status", value === "all" ? undefined : (value as DeviceStatus))
              }
            >
              <SelectTrigger>
                <SelectValue placeholder="Status" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All Statuses</SelectItem>
                <SelectItem value={DeviceStatus.CREATED}>{DeviceStatusLabels[DeviceStatus.CREATED]}</SelectItem>
                <SelectItem value={DeviceStatus.SETUP}>{DeviceStatusLabels[DeviceStatus.SETUP]}</SelectItem>
                <SelectItem value={DeviceStatus.STORED}>{DeviceStatusLabels[DeviceStatus.STORED]}</SelectItem>
                <SelectItem value={DeviceStatus.SCRAPPED}>{DeviceStatusLabels[DeviceStatus.SCRAPPED]}</SelectItem>
              </SelectContent>
            </Select>

            {/* Location Filter */}
            <Combobox
              options={locationOptions}
              value={filters.location}
              onValueChange={(value) => handleFilterChange("location", value)}
              placeholder="Location"
              searchPlaceholder="Search or type location..."
              emptyText="No exact matches"
              className="w-full"
              allowCustomValue={true}
            />

            {/* Product Category Filter */}
            <Combobox
              options={categoryOptions}
              value={filters.product_category}
              onValueChange={(value) => handleFilterChange("product_category", value)}
              placeholder="Product Category"
              searchPlaceholder="Search or type category..."
              emptyText="No exact matches"
              className="w-full"
              allowCustomValue={true}
            />

            {/* Product Name Filter */}
            <Combobox
              options={productOptions}
              value={filters.product_name}
              onValueChange={(value) => handleFilterChange("product_name", value)}
              placeholder="Product Name"
              searchPlaceholder="Search or type product..."
              emptyText="No exact matches"
              className="w-full"
              allowCustomValue={true}
            />

            {/* Manufacturer Filter */}
            <Combobox
              options={manufacturerOptions}
              value={filters.manufacturer}
              onValueChange={(value) => handleFilterChange("manufacturer", value)}
              placeholder="Manufacturer"
              searchPlaceholder="Search or type manufacturer..."
              emptyText="No exact matches"
              className="w-full"
              allowCustomValue={true}
            />

            {/* Sample Type Filter */}
            <Combobox
              options={sampleTypeOptions}
              value={filters.sample_type}
              onValueChange={(value) => handleFilterChange("sample_type", value)}
              placeholder="Sample Type"
              searchPlaceholder="Search or type sample type..."
              emptyText="No exact matches"
              className="w-full"
              allowCustomValue={true}
            />
          </div>

          {/* Clear Filters Link */}
          {hasActiveFilters && (
            <div className="flex justify-end">
              <button
                onClick={handleClearFilters}
                className="text-sm text-muted-foreground hover:text-foreground transition-colors flex items-center gap-1"
              >
                <X className="h-3.5 w-3.5" />
                Clear all filters
              </button>
            </div>
          )}
        </div>

        {/* Results Table */}
        <div className="flex-1 overflow-auto border rounded-md h-[400px]">
          {loading ? (
            <div className="h-full flex items-center justify-center p-4">
              <div className="space-y-3 w-full max-w-md">
                <Skeleton className="h-10 w-full" />
                <Skeleton className="h-10 w-full" />
                <Skeleton className="h-10 w-full" />
              </div>
            </div>
          ) : error ? (
            <div className="h-full flex items-center justify-center p-8 text-center text-destructive">
              <div>
                <p className="font-medium">Error loading Devices</p>
                <p className="text-sm text-muted-foreground">{error}</p>
              </div>
            </div>
          ) : devices.length === 0 ? (
            <div className="h-full flex items-center justify-center p-8 text-center text-muted-foreground">
              <div>
                <p className="font-medium">No Devices found</p>
                <p className="text-sm">Try adjusting your filters</p>
              </div>
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-12">
                    <Checkbox
                      checked={allSelected}
                      onCheckedChange={handleToggleAll}
                      aria-label="Select all"
                    />
                  </TableHead>
                  <TableHead>Device ID</TableHead>
                  <TableHead>Sample ID</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Product</TableHead>
                  <TableHead>Location</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {devices.map((device) => {
                  const isSelected = selected.has(device.device_id)
                  return (
                    <TableRow
                      key={device.device_id}
                      className={isSelected ? "bg-muted/50" : "cursor-pointer"}
                      onClick={() => handleToggleSelect(device.device_id)}
                    >
                      <TableCell onClick={(e) => e.stopPropagation()}>
                        <Checkbox
                          checked={isSelected}
                          onCheckedChange={() => handleToggleSelect(device.device_id)}
                          aria-label={`Select ${device.device_id}`}
                        />
                      </TableCell>
                      <TableCell className="font-medium">{device.device_id}</TableCell>
                      <TableCell>{device.sample_id}</TableCell>
                      <TableCell>
                        <DeviceStatusBadge status={device.status} />
                      </TableCell>
                      <TableCell>
                        <div className="text-sm">
                          <div>{device.product_name}</div>
                          <div className="text-muted-foreground">{device.manufacturer}</div>
                        </div>
                      </TableCell>
                      <TableCell>{device.location}</TableCell>
                    </TableRow>
                  )
                })}
              </TableBody>
            </Table>
          )}
        </div>

        {/* Footer */}
        <DialogFooter className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="text-sm text-muted-foreground">
              {selected.size > 0 ? (
                <Badge variant="secondary" className="text-sm">
                  {selected.size} Device{selected.size !== 1 ? "s" : ""} selected
                </Badge>
              ) : (
                <span>No Devices selected</span>
              )}
            </div>
            {selected.size > 0 && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setSelected(new Set())}
              >
                <X className="mr-2 h-4 w-4" />
                Clear Selection
              </Button>
            )}
          </div>
          <div className="flex gap-2">
            <Button variant="outline" onClick={handleCancel}>
              Cancel
            </Button>
            <Button onClick={handleConfirm} disabled={selected.size === 0}>
              Confirm Selection
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
