/**
 * Journal Metadata Dialog Component
 * Displays a preview of changes with JSON diff and allows user to add journal metadata
 * before saving device edits
 */

"use client"

import { useState, useEffect } from "react"
import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { z } from "zod"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { JsonDiffViewer } from "@/components/shared/json-diff-viewer"
import type { Device, DeviceUpdatePreview } from "@/types/device"
import { Loader2 } from "lucide-react"

interface JournalMetadataDialogProps {
  /** Whether the dialog is open */
  open: boolean
  /** Callback when dialog open state changes */
  onOpenChange: (open: boolean) => void
  /** The original device data (before edits) */
  originalData: Device
  /** The new device data (with edits applied) */
  newData: Device
  /** The preview data from backend (suggested text and changed fields) */
  preview: DeviceUpdatePreview | null
  /** Loading state while fetching preview */
  loading?: boolean
  /** Callback when user confirms save with journal text */
  onConfirm: (journalText: string) => void
}

const journalMetadataSchema = z.object({
  journal_text: z.string().min(1, "Please describe what changed and why"),
})

type JournalMetadataFormData = z.infer<typeof journalMetadataSchema>

export function JournalMetadataDialog({
  open,
  onOpenChange,
  originalData,
  newData,
  preview,
  loading = false,
  onConfirm,
}: JournalMetadataDialogProps) {
  const [isSubmitting, setIsSubmitting] = useState(false)

  const form = useForm<JournalMetadataFormData>({
    resolver: zodResolver(journalMetadataSchema),
    defaultValues: {
      journal_text: "",
    },
  })

  // Update form when preview changes
  useEffect(() => {
    if (preview?.suggested_text) {
      form.setValue("journal_text", preview.suggested_text)
    }
  }, [preview, form])

  const handleSubmit = async (data: JournalMetadataFormData) => {
    setIsSubmitting(true)
    try {
      await onConfirm(data.journal_text)
      form.reset()
    } finally {
      setIsSubmitting(false)
    }
  }

  const handleCancel = () => {
    form.reset()
    onOpenChange(false)
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-7xl max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="text-2xl">Confirm Device Changes</DialogTitle>
          <p className="text-sm text-muted-foreground">
            Review your changes and provide a description for the journal entry
          </p>
        </DialogHeader>

        <div className="space-y-6">
          {/* Loading state */}
          {loading && (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
            </div>
          )}

          {/* Preview with diff */}
          {!loading && preview && (
            <form onSubmit={form.handleSubmit(handleSubmit)} className="space-y-6">
              {/* Journal entry form - at the top */}
              <div className="space-y-2">
                <Label htmlFor="journal_text" className="text-base font-semibold">
                  Change Description
                </Label>
                <Textarea
                  id="journal_text"
                  {...form.register("journal_text")}
                  placeholder="Describe what changed and why (e.g., 'Updated device location to Lab 3')"
                  className="min-h-[120px] resize-none"
                />
                {form.formState.errors.journal_text && (
                  <p className="text-sm text-red-500">
                    {form.formState.errors.journal_text.message}
                  </p>
                )}
              </div>

              {/* JSON Diff */}
              <JsonDiffViewer
                oldData={originalData}
                newData={newData}
                title="Your Changes"
              />

              <DialogFooter>
                <Button
                  type="button"
                  variant="outline"
                  onClick={handleCancel}
                  disabled={isSubmitting}
                >
                  Cancel
                </Button>
                <Button type="submit" disabled={isSubmitting}>
                  {isSubmitting && (
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  )}
                  Save Changes
                </Button>
              </DialogFooter>
            </form>
          )}

          {/* Error state */}
          {!loading && !preview && (
            <div className="text-center py-12 text-muted-foreground">
              Failed to load preview. Please try again.
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
