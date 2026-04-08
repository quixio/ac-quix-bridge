/**
 * Dialog component for creating manual journal entries for devices
 */

"use client"

import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { useState } from "react"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Loader2 } from "lucide-react"
import {
  journalEntryCreateSchema,
  type JournalEntryCreateFormData,
} from "@/lib/schemas/device-schema"
import { JournalCategory } from "@/types/device"

interface JournalEntryFormDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onSubmit: (data: JournalEntryCreateFormData) => Promise<void>
}

export function JournalEntryFormDialog({
  open,
  onOpenChange,
  onSubmit,
}: JournalEntryFormDialogProps) {
  const [isSubmitting, setIsSubmitting] = useState(false)

  const form = useForm<JournalEntryCreateFormData>({
    resolver: zodResolver(journalEntryCreateSchema),
    defaultValues: {
      category: null,
      text: "",
    },
  })

  const handleSubmit = async (data: JournalEntryCreateFormData) => {
    try {
      setIsSubmitting(true)
      await onSubmit(data)
      // Reset form and close dialog on success
      form.reset()
      onOpenChange(false)
    } catch (error) {
      console.error("Failed to create journal entry:", error)
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
      <DialogContent className="sm:max-w-[600px]">
        <DialogHeader>
          <DialogTitle>Add Journal Entry</DialogTitle>
          <DialogDescription>
            Create a manual journal entry to document observations or notes about this device.
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={form.handleSubmit(handleSubmit)} className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="category">Category (Optional)</Label>
            <Select
              value={form.watch("category") || "none"}
              onValueChange={(value) =>
                form.setValue("category", value === "none" ? null : (value as JournalCategory))
              }
            >
              <SelectTrigger id="category">
                <SelectValue placeholder="Select category (optional)" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="none">None</SelectItem>
                {Object.values(JournalCategory).map((cat) => (
                  <SelectItem key={cat} value={cat}>
                    {cat}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-2">
            <Label htmlFor="text">Journal Text *</Label>
            <Textarea
              id="text"
              {...form.register("text")}
              placeholder="Describe the observation or note..."
              rows={5}
            />
            {form.formState.errors.text && (
              <p className="text-sm text-red-500">
                {form.formState.errors.text.message}
              </p>
            )}
          </div>

          <DialogFooter>
            <Button type="button" variant="outline" onClick={handleCancel} disabled={isSubmitting}>
              Cancel
            </Button>
            <Button type="submit" disabled={isSubmitting}>
              {isSubmitting && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Add Entry
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
