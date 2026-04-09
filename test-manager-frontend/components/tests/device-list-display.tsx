import { useState, useEffect } from "react"
import Link from "next/link"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import { DeviceStatusBadge } from "@/components/devices/device-status-badge"
import { useDevicesApi } from "@/lib/hooks/use-api"
import type { DeviceReference } from "@/types/test"
import type { Device } from "@/types/device"
import type { ReactNode } from "react"

interface DeviceListDisplayProps {
  devices: DeviceReference[]
  testId: string
  headerAction?: ReactNode
}

export function DeviceListDisplay({ devices, testId, headerAction }: DeviceListDisplayProps) {
  const devicesApi = useDevicesApi()
  const [deviceDetails, setDeviceDetails] = useState<Device[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Fetch full Device details
  useEffect(() => {
    const fetchDeviceDetails = async () => {
      if (devices.length === 0) {
        setLoading(false)
        return
      }

      setLoading(true)
      setError(null)
      try {
        // Fetch all Devices in a single batch request (optimized)
        const deviceIds = devices.map((ref) => ref.device_id)
        const results = await devicesApi.getBatch(deviceIds)
        setDeviceDetails(results)
      } catch (err) {
        console.error("Failed to fetch Device details:", err)
        setError("Failed to load Device details")
      } finally {
        setLoading(false)
      }
    }

    fetchDeviceDetails()
  }, [devices])
  if (devices.length === 0) {
    return (
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle className="text-base">Devices (Devices Under Test)</CardTitle>
            {headerAction}
          </div>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">No Devices assigned to this test.</p>
        </CardContent>
      </Card>
    )
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle className="text-base">
            Devices (Devices Under Test) - {devices.length}
          </CardTitle>
          {headerAction}
        </div>
      </CardHeader>
      <CardContent>
        {loading ? (
          <div className="space-y-3">
            {devices.map((_, index) => (
              <div key={index} className="border rounded-lg p-3">
                <Skeleton className="h-4 w-32 mb-2" />
                <Skeleton className="h-3 w-24 mb-1" />
                <Skeleton className="h-3 w-40" />
              </div>
            ))}
          </div>
        ) : error ? (
          <p className="text-sm text-destructive">{error}</p>
        ) : (
          <div className="space-y-3">
            {deviceDetails.map((deviceDetail, index) => {
              const deviceRef = devices[index]
              return (
                <Link
                  key={deviceDetail.device_id}
                  href={`/devices/${deviceDetail.device_id}`}
                  className="block"
                >
                  <div className="border rounded-lg p-3 hover:bg-muted/50 transition-colors cursor-pointer">
                    <div className="space-y-2">
                      {/* Header row: Device ID and Status */}
                      <div className="flex items-center justify-between">
                        <span className="font-medium">{deviceDetail.device_id}</span>
                        <DeviceStatusBadge status={deviceDetail.status} />
                      </div>

                      {/* Name and category */}
                      <div className="text-sm">
                        {deviceDetail.name}
                      </div>

                      {/* Version (if captured) */}
                      {deviceRef.device_version && (
                        <div className="text-xs text-muted-foreground">
                          Version: {deviceRef.device_version.substring(0, 8)}...
                        </div>
                      )}
                    </div>
                  </div>
                </Link>
              )
            })}
          </div>
        )}
      </CardContent>
    </Card>
  )
}
