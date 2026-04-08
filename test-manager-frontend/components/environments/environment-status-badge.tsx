import { Badge } from "@/components/ui/badge"

type EnvironmentStatus = "Active" | "Maintenance" | "Inactive"

interface EnvironmentStatusBadgeProps {
  status: EnvironmentStatus
}

export function EnvironmentStatusBadge({ status }: EnvironmentStatusBadgeProps) {
  const getVariant = (status: EnvironmentStatus) => {
    switch (status) {
      case "Active":
        return "success"
      case "Maintenance":
        return "warning"
      case "Inactive":
        return "secondary"
      default:
        return "secondary"
    }
  }

  return <Badge variant={getVariant(status)}>{status}</Badge>
}
