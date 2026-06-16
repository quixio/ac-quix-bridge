import { memo } from "react";
import { Badge } from "@/components/ui/badge";
import {
  EnvironmentStatus,
  EnvironmentStatusLabels,
} from "@/types/environment";

interface EnvironmentStatusBadgeProps {
  status: EnvironmentStatus;
}

export const EnvironmentStatusBadge = memo(function EnvironmentStatusBadge({
  status,
}: EnvironmentStatusBadgeProps) {
  const variants = {
    [EnvironmentStatus.ACTIVE]: {
      variant: "success" as const,
      label: EnvironmentStatusLabels[EnvironmentStatus.ACTIVE],
    },
    [EnvironmentStatus.INACTIVE]: {
      variant: "destructive" as const,
      label: EnvironmentStatusLabels[EnvironmentStatus.INACTIVE],
    },
  };

  const config = variants[status] || variants[EnvironmentStatus.ACTIVE];

  return (
    <Badge variant={config.variant} className="font-medium">
      {config.label}
    </Badge>
  );
});
