import { memo } from "react";
import { Badge } from "@/components/ui/badge";
import { DeviceStatus, DeviceStatusLabels } from "@/types/device";

interface DeviceStatusBadgeProps {
  status: DeviceStatus;
}

export const DeviceStatusBadge = memo(function DeviceStatusBadge({
  status,
}: DeviceStatusBadgeProps) {
  const variants = {
    [DeviceStatus.ACTIVE]: {
      variant: "success" as const,
      label: DeviceStatusLabels[DeviceStatus.ACTIVE],
    },
    [DeviceStatus.INACTIVE]: {
      variant: "destructive" as const,
      label: DeviceStatusLabels[DeviceStatus.INACTIVE],
    },
  };

  const config = variants[status] || variants[DeviceStatus.ACTIVE];

  return (
    <Badge variant={config.variant} className="font-medium">
      {config.label}
    </Badge>
  );
});
