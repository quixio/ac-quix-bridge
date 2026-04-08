/**
 * Version Comparison Dialog Component
 * Displays a journal entry with side-by-side JSON diff and navigation between entries
 */

"use client"

import { useState, useEffect } from "react"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { JsonDiffViewer } from "@/components/shared/json-diff-viewer"
import { ChevronLeft, ChevronRight, X } from "lucide-react"
import type { DeviceJournalEntry } from "@/types/device"
import { formatDistanceToNow } from "date-fns"
import {
  formatJournalCategory,
  getJournalCategoryColor,
} from "@/lib/utils/device"

interface VersionComparisonDialogProps {
  /** Whether the dialog is open */
  open: boolean
  /** Callback when dialog open state changes */
  onOpenChange: (open: boolean) => void
  /** All journal entries sorted chronologically (newest first) */
  entries: DeviceJournalEntry[]
  /** Index of the entry to display initially */
  initialIndex: number
}

export function VersionComparisonDialog({
  open,
  onOpenChange,
  entries,
  initialIndex,
}: VersionComparisonDialogProps) {
  const [currentIndex, setCurrentIndex] = useState(initialIndex)

  // Reset to initial index when dialog opens or initialIndex changes
  useEffect(() => {
    if (open) {
      setCurrentIndex(initialIndex)
    }
  }, [open, initialIndex])

  if (entries.length === 0) {
    return null
  }

  const currentEntry = entries[currentIndex]
  const previousEntry = currentIndex < entries.length - 1 ? entries[currentIndex + 1] : null

  const hasPrevious = currentIndex < entries.length - 1
  const hasNext = currentIndex > 0

  const handlePrevious = () => {
    if (hasPrevious) {
      setCurrentIndex(currentIndex + 1)
    }
  }

  const handleNext = () => {
    if (hasNext) {
      setCurrentIndex(currentIndex - 1)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-7xl max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <div className="flex items-start justify-between">
            <div className="space-y-2">
              <DialogTitle className="text-2xl">Version Comparison</DialogTitle>
              <div className="flex items-center gap-3 text-sm text-muted-foreground">
                <span key="entry-info">
                  Entry {currentIndex + 1} of {entries.length}
                </span>
                <span key="sep-1">•</span>
                <span key="timestamp">
                  {formatDistanceToNow(new Date(currentEntry.timestamp), {
                    addSuffix: true,
                  })}
                </span>
                <span key="sep-2">•</span>
                <span key="editor" className="font-medium">{currentEntry.editor}</span>
                {currentEntry.category && (
                  <>
                    <span key="sep-3">•</span>
                    <Badge
                      key="category"
                      variant="outline"
                      className={`bg-${getJournalCategoryColor(currentEntry.category)}-500/10 text-${getJournalCategoryColor(currentEntry.category)}-600 border-${getJournalCategoryColor(currentEntry.category)}-500/20`}
                    >
                      {formatJournalCategory(currentEntry.category)}
                    </Badge>
                  </>
                )}
              </div>
            </div>
          </div>
        </DialogHeader>

        <div className="space-y-4">
          {/* Journal entry text if present */}
          {currentEntry.text && (
            <div className="rounded-lg border bg-muted/50 p-4">
              <p className="text-sm font-medium text-muted-foreground mb-2">
                Change Description
              </p>
              <p className="text-sm whitespace-pre-wrap">{currentEntry.text}</p>
            </div>
          )}

          {/* JSON diff */}
          <JsonDiffViewer
            oldData={previousEntry?.data || {}}
            newData={currentEntry.data}
            title={
              previousEntry
                ? `Changes from Version ${currentIndex + 2}`
                : "Initial Version"
            }
          />

          {/* Navigation controls */}
          <div className="flex items-center justify-between pt-4 border-t">
            <Button
              variant="outline"
              onClick={handlePrevious}
              disabled={!hasPrevious}
            >
              <ChevronLeft className="mr-2 h-4 w-4" />
              Previous Version
            </Button>

            <div className="text-sm text-muted-foreground">
              Version {entries.length - currentIndex} of {entries.length}
            </div>

            <Button
              variant="outline"
              onClick={handleNext}
              disabled={!hasNext}
            >
              Next Version
              <ChevronRight className="ml-2 h-4 w-4" />
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
