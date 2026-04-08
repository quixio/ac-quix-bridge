"use client"

import { Button } from "@/components/ui/button"
import { ArrowLeftRight, X, Loader2 } from "lucide-react"
import { cn } from "@/lib/utils"
import type { TopicReference } from "@/lib/types/portal"

interface TopicDisplayProps {
  topic: TopicReference | null
  isFallback?: boolean
  isClearing?: boolean
  onChange: () => void
  onClear: () => void
  className?: string
}

export function TopicDisplay({
  topic,
  isFallback = false,
  isClearing = false,
  onChange,
  onClear,
  className,
}: TopicDisplayProps) {
  if (!topic) {
    return null
  }

  return (
    <div className={cn("flex items-center justify-between p-3 border rounded-md bg-muted/30", className)}>
      <div className="flex items-center gap-3 min-w-0 flex-1">
        <ArrowLeftRight className={cn("h-5 w-5 flex-shrink-0", isFallback ? "text-blue-500" : "text-green-500")} />
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="font-medium truncate">{topic.topic_name}</span>
            {isFallback && (
              <span className="text-xs px-1.5 py-0.5 rounded bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400">
                Default
              </span>
            )}
          </div>
          <div className="text-sm text-muted-foreground truncate">
            Workspace: {topic.workspace_name || topic.workspace_id}
          </div>
        </div>
      </div>

      <div className="flex gap-1 flex-shrink-0 ml-2">
        <Button
          variant="outline"
          size="sm"
          onClick={onChange}
        >
          Change
        </Button>
        {!isFallback && (
          <Button
            variant="ghost"
            size="sm"
            onClick={onClear}
            disabled={isClearing}
            title="Clear selection"
          >
            {isClearing ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <X className="h-4 w-4" />
            )}
          </Button>
        )}
      </div>
    </div>
  )
}
