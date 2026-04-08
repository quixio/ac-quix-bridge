"use client"

import { Card, CardHeader } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import type { DeviceJournalEntry } from "@/types/device"
import {
  formatJournalCategory,
  getJournalCategoryColor,
  getJournalCategoryClasses,
} from "@/lib/utils/device"
import { Eye } from "lucide-react"
import { cn } from "@/lib/utils/cn"
import { useDateFormatter } from "@/lib/hooks/use-date-formatter"

interface JournalEntryCardProps {
  entry: DeviceJournalEntry
  onClick?: () => void
}

export function JournalEntryCard({ entry, onClick }: JournalEntryCardProps) {
  const { formatRelative, formatDateTime } = useDateFormatter()
  const categoryColor = getJournalCategoryColor(entry.category)
  const categoryLabel = formatJournalCategory(entry.category)
  const categoryClasses = getJournalCategoryClasses(entry.category)

  // Format timestamp
  const timeAgo = formatRelative(entry.timestamp)

  // Get border color from journal category
  const getBorderColor = () => {
    if (!categoryColor) return "hsl(var(--border))"
    return `hsl(var(--journal-${categoryColor}))`
  }

  return (
    <Card
      className="group border-l-4 cursor-pointer transition-colors hover:bg-accent/50"
      style={{ borderLeftColor: getBorderColor() }}
      onClick={onClick}
    >
      <CardHeader className="pb-3">
        <div className="flex items-start justify-between gap-4">
          <div className="flex-1">
            {/* Category and timestamp */}
            <div className="flex items-center gap-2 flex-wrap mb-2">
              <Badge
                variant="outline"
                className={cn(categoryClasses)}
              >
                {categoryLabel}
              </Badge>
              <span className="text-sm text-muted-foreground">
                {timeAgo}
              </span>
            </div>

            {/* Editor */}
            <div className="text-sm text-muted-foreground mb-2">
              by <span className="font-medium text-foreground">{entry.editor}</span>
            </div>

            {/* Description */}
            <p className="text-sm font-medium mb-2">{entry.text}</p>

            {/* Exact timestamp */}
            <div className="text-xs text-muted-foreground">
              {formatDateTime(entry.timestamp)}
            </div>
          </div>

          {/* Eye icon to indicate clickable - only visible on hover */}
          <Eye className="h-5 w-5 text-muted-foreground shrink-0 opacity-0 group-hover:opacity-100 transition-opacity" />
        </div>
      </CardHeader>
    </Card>
  )
}
