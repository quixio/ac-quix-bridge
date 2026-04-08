/**
 * Device Status Badge Component
 * Displays a colored badge for Device status with industrial professional colors
 */

import { memo } from "react"
import { Badge } from "@/components/ui/badge"
import { DeviceStatus, DeviceStatusLabels } from "@/types/device"

interface DeviceStatusBadgeProps {
  status: DeviceStatus
}

export const DeviceStatusBadge = memo(function DeviceStatusBadge({ status }: DeviceStatusBadgeProps) {
  const variants = {
    [DeviceStatus.CREATED]: {
      variant: "info" as const,
      label: DeviceStatusLabels[DeviceStatus.CREATED],
    },
    [DeviceStatus.SETUP]: {
      variant: "warning" as const,
      label: DeviceStatusLabels[DeviceStatus.SETUP],
    },
    [DeviceStatus.STORED]: {
      variant: "success" as const,
      label: DeviceStatusLabels[DeviceStatus.STORED],
    },
    [DeviceStatus.SCRAPPED]: {
      variant: "destructive" as const,
      label: DeviceStatusLabels[DeviceStatus.SCRAPPED],
    },
  }

  const config = variants[status] || variants[DeviceStatus.CREATED]

  return (
    <Badge variant={config.variant} className="font-medium">
      {config.label}
    </Badge>
  )
})
