"use client"

import { useEffect } from "react"
import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import * as z from "zod"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { DateTimePicker } from "@/components/ui/datetime-picker"
import type { LogbookEntry, LogbookEntryCreate, LogbookEntryUpdate } from "@/types/test"

const logbookEntrySchema = z.object({
  operator: z.string().min(1, "Operator is required"),
  content: z.string().min(1, "Content is required"),
  timestamp: z.date().optional(),
  sensor_ids: z.string().optional(), // Comma-separated string, will be split
})

type LogbookEntryFormData = z.infer<typeof logbookEntrySchema>

interface LogbookEntryFormProps {
  testId: string
  entry?: LogbookEntry // If provided, we're editing
  defaultOperator?: string
  onSubmit: (data: LogbookEntryCreate | LogbookEntryUpdate) => Promise<void>
  onCancel: () => void
  isSubmitting?: boolean
}

export function LogbookEntryForm({
  testId,
  entry,
  defaultOperator,
  onSubmit,
  onCancel,
  isSubmitting = false,
}: LogbookEntryFormProps) {
  const {
    register,
    handleSubmit,
    setValue,
    watch,
    setFocus,
    formState: { errors },
  } = useForm<LogbookEntryFormData>({
    resolver: zodResolver(logbookEntrySchema),
    defaultValues: {
      operator: entry?.operator || defaultOperator || "",
      content: entry?.content || "",
      timestamp: entry?.timestamp ? new Date(entry.timestamp) : new Date(),
      sensor_ids: entry?.sensor_ids?.join(", ") || "",
    },
  })

  const timestamp = watch("timestamp")

  // Auto-focus on content field when form opens
  useEffect(() => {
    setFocus("content")
  }, [setFocus])

  const handleFormSubmit = async (data: LogbookEntryFormData) => {
    // Convert sensor_ids string to array
    const sensor_ids = data.sensor_ids
      ? data.sensor_ids
          .split(",")
          .map((s) => s.trim())
          .filter((s) => s.length > 0)
      : []

    const submitData: LogbookEntryCreate | LogbookEntryUpdate = {
      operator: data.operator,
      content: data.content,
      timestamp: data.timestamp ? data.timestamp.toISOString() : new Date().toISOString(),
      sensor_ids: sensor_ids.length > 0 ? sensor_ids : undefined,
    }

    await onSubmit(submitData)
  }

  return (
    <form onSubmit={handleSubmit(handleFormSubmit)} className="space-y-5">
      {/* Operator */}
      <div className="space-y-2">
        <Label htmlFor="operator">Operator *</Label>
        <Input
          id="operator"
          {...register("operator")}
          placeholder="Enter operator name"
        />
        {errors.operator && (
          <p className="text-sm text-destructive mt-1.5">{errors.operator.message}</p>
        )}
      </div>

      {/* Timestamp */}
      <div className="space-y-2">
        <Label htmlFor="timestamp">Timestamp</Label>
        <DateTimePicker
          value={timestamp}
          onChange={(date) => setValue("timestamp", date, { shouldValidate: true })}
          disabled={isSubmitting}
          placeholder="Pick date and time"
        />
        {errors.timestamp && (
          <p className="text-sm text-destructive mt-1.5">{errors.timestamp.message}</p>
        )}
        {!errors.timestamp && (
          <p className="text-xs text-muted-foreground">
            Defaults to current time if left unchanged
          </p>
        )}
      </div>

      {/* Content */}
      <div className="space-y-2">
        <Label htmlFor="content">Content *</Label>
        <Textarea
          id="content"
          {...register("content")}
          placeholder="Describe what happened during the test..."
          rows={6}
        />
        {errors.content && (
          <p className="text-sm text-destructive mt-1.5">{errors.content.message}</p>
        )}
      </div>

      {/* Sensor IDs */}
      <div className="space-y-2">
        <Label htmlFor="sensor_ids">Sensor IDs (optional)</Label>
        <Input
          id="sensor_ids"
          {...register("sensor_ids")}
          placeholder="e.g., temp_1, pressure_2, flow_3"
        />
        {errors.sensor_ids && (
          <p className="text-sm text-destructive mt-1.5">{errors.sensor_ids.message}</p>
        )}
        {!errors.sensor_ids && (
          <p className="text-xs text-muted-foreground">
            Comma-separated list of sensor identifiers
          </p>
        )}
      </div>

      {/* Actions */}
      <div className="flex justify-end gap-2 pt-4">
        <Button
          type="button"
          variant="outline"
          onClick={onCancel}
          disabled={isSubmitting}
        >
          Cancel
        </Button>
        <Button type="submit" disabled={isSubmitting}>
          {isSubmitting ? "Saving..." : entry ? "Update Entry" : "Create Entry"}
        </Button>
      </div>
    </form>
  )
}
