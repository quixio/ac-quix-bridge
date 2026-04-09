"use client"

import { useState, useEffect } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
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
import { DeviceStatus, DeviceCategory, DeviceCategoryLabels } from "@/types/device"
import type { Device, DeviceQuery } from "@/types/device"
import type { DeviceReference } from "@/types/test"
import { X, Search } from "lucide-react"
import { DeviceStatusBadge } from "@/components/devices/device-status-badge"

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

  const [filters, setFilters] = useState<DeviceQuery>({})
  const [devices, setDevices] = useState<Device[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())

  useEffect(() => {
    if (open) {
      const selectedIds = new Set(selectedDevices.map((d) => d.device_id))
      setSelected(selectedIds)
    }
  }, [open, selectedDevices])

  useEffect(() => {
    if (!open) return

    const fetchDevices = async () => {
      setLoading(true)
      setError(null)
      try {
        const result = await devicesApi.list({ ...filters, page_size: 100 })
        setDevices(result.items)
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to fetch devices")
      } finally {
        setLoading(false)
      }
    }

    const debounce = setTimeout(fetchDevices, 300)
    return () => clearTimeout(debounce)
  }, [filters, open])

  const handleFilterChange = (key: string, value: string | undefined) => {
    setFilters((prev) => ({ ...prev, [key]: value || undefined }))
  }

  const toggleDevice = (deviceId: string) => {
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

  const handleConfirm = () => {
    const deviceRefs: DeviceReference[] = Array.from(selected).map((id) => {
      const existing = selectedDevices.find((d) => d.device_id === id)
      return existing || { device_id: id, device_version: null }
    })
    onConfirm(deviceRefs)
    onOpenChange(false)
  }

  const clearFilters = () => setFilters({})

  const hasFilters = filters.q || filters.category || filters.status

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl max-h-[80vh] flex flex-col">
        <DialogHeader>
          <DialogTitle>Select Devices</DialogTitle>
          <DialogDescription>
            Choose devices for this test. {selected.size} selected.
          </DialogDescription>
        </DialogHeader>

        {/* Filters */}
        <div className="flex gap-3 items-center">
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
            <Input
              placeholder="Search devices..."
              className="pl-9"
              value={filters.q || ""}
              onChange={(e) => handleFilterChange("q", e.target.value)}
            />
          </div>
          <Select
            value={filters.category || "all"}
            onValueChange={(v) => handleFilterChange("category", v === "all" ? undefined : v)}
          >
            <SelectTrigger className="w-[150px]">
              <SelectValue placeholder="Category" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All</SelectItem>
              <SelectItem value={DeviceCategory.PC}>{DeviceCategoryLabels[DeviceCategory.PC]}</SelectItem>
              <SelectItem value={DeviceCategory.TEST_RIG}>{DeviceCategoryLabels[DeviceCategory.TEST_RIG]}</SelectItem>
            </SelectContent>
          </Select>
          {hasFilters && (
            <Button variant="ghost" size="sm" onClick={clearFilters}>
              <X className="h-4 w-4 mr-1" /> Clear
            </Button>
          )}
        </div>

        {/* Table */}
        <div className="flex-1 overflow-auto border rounded-md">
          {loading ? (
            <div className="p-4 space-y-2">
              <Skeleton className="h-8 w-full" />
              <Skeleton className="h-8 w-full" />
              <Skeleton className="h-8 w-full" />
            </div>
          ) : error ? (
            <div className="p-4 text-center text-destructive">{error}</div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-12" />
                  <TableHead>Device ID</TableHead>
                  <TableHead>Category</TableHead>
                  <TableHead>Name</TableHead>
                  <TableHead>Status</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {devices.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={5} className="text-center h-24">
                      No devices found.
                    </TableCell>
                  </TableRow>
                ) : (
                  devices.map((device) => (
                    <TableRow
                      key={device.device_id}
                      className="cursor-pointer"
                      onClick={() => toggleDevice(device.device_id)}
                    >
                      <TableCell>
                        <Checkbox checked={selected.has(device.device_id)} />
                      </TableCell>
                      <TableCell className="font-medium">{device.device_id}</TableCell>
                      <TableCell>
                        <Badge variant="outline">
                          {DeviceCategoryLabels[device.category] || device.category}
                        </Badge>
                      </TableCell>
                      <TableCell>{device.name}</TableCell>
                      <TableCell>
                        <DeviceStatusBadge status={device.status} />
                      </TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={handleConfirm}>
            Confirm ({selected.size} selected)
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
