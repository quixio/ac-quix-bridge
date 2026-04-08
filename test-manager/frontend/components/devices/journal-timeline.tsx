"use client"

import { useState } from "react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { EmptyState } from "@/components/shared/empty-state"
import { JournalEntryCard } from "./journal-entry-card"
import { VersionComparisonDialog } from "./version-comparison-dialog"
import type { DeviceJournalEntry } from "@/types/device"
import { ScrollText } from "lucide-react"

interface JournalTimelineProps {
  entries: DeviceJournalEntry[]
  loading?: boolean
  error?: Error | null
  headerAction?: React.ReactNode
}

export function JournalTimeline({
  entries,
  loading = false,
  error = null,
  headerAction,
}: JournalTimelineProps) {
  const [selectedEntryIndex, setSelectedEntryIndex] = useState<number | null>(null)

  // Sort entries by timestamp (newest first)
  const sortedEntries = [...entries].sort((a, b) => {
    return new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime()
  })

  const handleEntryClick = (index: number) => {
    setSelectedEntryIndex(index)
  }

  return (
    <>
      <Card>
        <CardHeader>
          <div className="flex items-start justify-between">
            <div className="space-y-1">
              <CardTitle>Journal Timeline</CardTitle>
              <p className="text-sm text-muted-foreground">
                Complete history of changes and events for this device
              </p>
            </div>
            {headerAction && <div className="ml-4">{headerAction}</div>}
          </div>
        </CardHeader>
        <CardContent>
          {loading ? (
            <div className="space-y-4">
              {[1, 2, 3].map((i) => (
                <Skeleton key={i} className="h-32 w-full" />
              ))}
            </div>
          ) : error ? (
            <EmptyState
              icon={<ScrollText className="h-12 w-12" />}
              title="Failed to load journal"
              description={error.message}
            />
          ) : sortedEntries.length === 0 ? (
            <EmptyState
              icon={<ScrollText className="h-12 w-12" />}
              title="No journal entries"
              description="No journal entries have been created for this device yet."
            />
          ) : (
            <div className="space-y-4">
              {sortedEntries.map((entry, index) => (
                <JournalEntryCard
                  key={entry._id}
                  entry={entry}
                  onClick={() => handleEntryClick(index)}
                />
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Version comparison dialog */}
      <VersionComparisonDialog
        open={selectedEntryIndex !== null}
        onOpenChange={(open) => !open && setSelectedEntryIndex(null)}
        entries={sortedEntries}
        initialIndex={selectedEntryIndex ?? 0}
      />
    </>
  )
}
