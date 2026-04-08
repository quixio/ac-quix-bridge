import { memo } from "react"
import { Badge } from "@/components/ui/badge"
import { TestStatus } from "@/types/test"

interface TestStatusBadgeProps {
  status: TestStatus
}

export const TestStatusBadge = memo(function TestStatusBadge({ status }: TestStatusBadgeProps) {
  const variants = {
    [TestStatus.DRAFT]: {
      variant: "secondary" as const,
      label: "Draft",
    },
    [TestStatus.IN_PROGRESS]: {
      variant: "info" as const,
      label: "In Progress",
    },
    [TestStatus.FINISHED]: {
      variant: "success" as const,
      label: "Finished",
    },
  }

  const config = variants[status] || variants[TestStatus.DRAFT]

  return <Badge variant={config.variant}>{config.label}</Badge>
})
