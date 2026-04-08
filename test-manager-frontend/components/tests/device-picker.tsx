"use client"

import { useState, useEffect } from "react"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Card } from "@/components/ui/card"
import { useDevicesApi } from "@/lib/hooks/use-api"
import type { Device } from "@/types/device"
import { X, Search, Filter } from "lucide-react"
import type { DeviceReference } from "@/types/test"
import { DevicePickerDialog } from "./device-picker-dialog"

interface DevicePickerProps {
  value: DeviceReference[]
  onChange: (devices: DeviceReference[]) => void
  error?: string
}

export function DevicePicker({ value, onChange, error }: DevicePickerProps) {
  const devicesApi = useDevicesApi()
  const [searchQuery, setSearchQuery] = useState("")
  const [availableDevices, setAvailableDevices] = useState<Device[]>([])
  const [loading, setLoading] = useState(false)
  const [showResults, setShowResults] = useState(false)
  const [searchError, setSearchError] = useState<string | null>(null)
  const [showAdvancedDialog, setShowAdvancedDialog] = useState(false)

  // Fetch Devices when search query changes
  useEffect(() => {
    if (searchQuery.length < 2) {
      setAvailableDevices([])
      setShowResults(false)
      return
    }

    const fetchDevices = async () => {
      setLoading(true)
      setSearchError(null)
      try {
        const response = await devicesApi.list({ id_search: searchQuery })
        // Filter out already selected Devices
        const selectedIds = value.map((d) => d.device_id)
        setAvailableDevices(response.items.filter((d) => !selectedIds.includes(d.device_id)))
        setShowResults(true)
      } catch (err) {
        console.error("Failed to fetch Devices:", err)
        setSearchError("Failed to search Devices. Please try again.")
        setShowResults(true) // Show error message
      } finally {
        setLoading(false)
      }
    }

    const debounce = setTimeout(fetchDevices, 300)
    return () => clearTimeout(debounce)
  }, [searchQuery, value])

  const handleAdd = (device: Device) => {
    const newDevice: DeviceReference = {
      device_id: device.device_id,
      device_version: null,
    }
    onChange([...value, newDevice])
    setSearchQuery("")
    setShowResults(false)
  }

  const handleRemove = (deviceId: string) => {
    onChange(value.filter((d) => d.device_id !== deviceId))
  }

  return (
    <div className="space-y-3">
      {/* Selected Devices */}
      {value.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {value.map((device, index) => (
            <Badge key={index} variant="secondary" className="pl-3 pr-1 py-1">
              <span>{device.device_id}</span>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-auto p-1 ml-1 hover:bg-transparent"
                onClick={() => handleRemove(device.device_id)}
              >
                <X className="h-3 w-3" />
              </Button>
            </Badge>
          ))}
        </div>
      )}

      {/* Search Input */}
      <div className="relative">
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <Input
            type="text"
            placeholder="Quick search by Device ID or Sample ID..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="pl-10"
          />
        </div>

        {/* Search Results */}
        {showResults && (
          <Card className="absolute z-10 w-full mt-1 max-h-60 overflow-auto">
            {loading ? (
              <div className="p-4 text-sm text-muted-foreground">Searching...</div>
            ) : searchError ? (
              <div className="p-4 text-sm text-destructive">
                {searchError}
              </div>
            ) : availableDevices.length === 0 ? (
              <div className="p-4 text-sm text-muted-foreground">
                No Devices found
              </div>
            ) : (
              <div className="py-2">
                {availableDevices.map((device) => (
                  <button
                    key={device.device_id}
                    type="button"
                    data-device-result={device.device_id}
                    onClick={() => handleAdd(device)}
                    className="w-full px-4 py-2 text-left hover:bg-muted transition-colors"
                  >
                    <div className="font-medium">{device.device_id}</div>
                    {device.sample_id && (
                      <div className="text-sm text-muted-foreground">
                        {device.sample_id}
                      </div>
                    )}
                  </button>
                ))}
              </div>
            )}
          </Card>
        )}
      </div>

      {/* Advanced Filters Button */}
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={() => setShowAdvancedDialog(true)}
        className="w-full"
      >
        <Filter className="mr-2 h-4 w-4" />
        Advanced Filters
      </Button>

      {/* Advanced Device Picker Dialog */}
      <DevicePickerDialog
        open={showAdvancedDialog}
        onOpenChange={setShowAdvancedDialog}
        selectedDevices={value}
        onConfirm={onChange}
      />

      {error && <p className="text-sm text-destructive">{error}</p>}
    </div>
  )
}
