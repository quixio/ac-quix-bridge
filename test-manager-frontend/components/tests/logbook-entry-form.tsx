"use client"

import { useEffect } from "react"
import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import * as z from "zod"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import type { LogbookEntry, LogbookEntryCreate, LogbookEntryUpdate } from "@/types/test"

const logbookEntrySchema = z.object({
  content: z.string().min(1, "Content is required"),
})

type LogbookEntryFormData = z.infer<typeof logbookEntrySchema>

interface LogbookEntryFormProps {
  testId: string
  entry?: LogbookEntry
  defaultOperator?: string
  onSubmit: (data: LogbookEntryCreate | LogbookEntryUpdate) => Promise<void>
  onCancel: () => void
  isSubmitting?: boolean
}

export function LogbookEntryForm({
  testId,
  entry,
  onSubmit,
  onCancel,
  isSubmitting = false,
}: LogbookEntryFormProps) {
  const {
    register,
    handleSubmit,
    setFocus,
    formState: { errors },
  } = useForm<LogbookEntryFormData>({
    resolver: zodResolver(logbookEntrySchema),
    defaultValues: {
      content: entry?.content || "",
    },
  })

  useEffect(() => {
    setFocus("content")
  }, [setFocus])

  const handleFormSubmit = async (data: LogbookEntryFormData) => {
    await onSubmit({ content: data.content })
  }

  return (
    <form onSubmit={handleSubmit(handleFormSubmit)} className="space-y-5">
      <div className="space-y-2">
        <Label htmlFor="content">Content *</Label>
        <Textarea
          id="content"
          {...register("content")}
          placeholder="Add a remark about the session..."
          rows={4}
        />
        {errors.content && (
          <p className="text-sm text-destructive mt-1.5">{errors.content.message}</p>
        )}
      </div>

      <div className="flex justify-end gap-2 pt-2">
        <Button type="button" variant="outline" onClick={onCancel} disabled={isSubmitting}>
          Cancel
        </Button>
        <Button type="submit" disabled={isSubmitting}>
          {isSubmitting ? "Saving..." : entry ? "Update Entry" : "Add Entry"}
        </Button>
      </div>
    </form>
  )
}
