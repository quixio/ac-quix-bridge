"use client"

import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { JsonEditor } from "@/components/shared/json-editor"
import { DevicePicker } from "./device-picker"
import { DateTimePicker } from "@/components/ui/datetime-picker"
import { testCreateSchema, TestCreateInput } from "@/lib/schemas/test-schema"
import { TestStatus } from "@/types/test"
import type { Test } from "@/types/test"

interface TestFormProps {
  initialData?: Test
  onSubmit: (data: TestCreateInput) => void | Promise<void>
  onCancel: () => void
  isSubmitting?: boolean
}

export function TestForm({ initialData, onSubmit, onCancel, isSubmitting = false }: TestFormProps) {
  const {
    register,
    handleSubmit,
    setValue,
    watch,
    formState: { errors },
  } = useForm<TestCreateInput>({
    resolver: zodResolver(testCreateSchema),
    defaultValues: initialData
      ? {
          test_id: initialData.test_id,
          campaign_id: initialData.campaign_id,
          devices: initialData.devices,
          environment_id: initialData.environment_id,
          operator: initialData.operator,
          sensors: initialData.sensors,
          status: initialData.status,
          start: initialData.start ? new Date(initialData.start) : undefined,
          end: initialData.end ? new Date(initialData.end) : undefined,
        }
      : {
          test_id: "",
          campaign_id: "",
          devices: [],
          environment_id: "",
          operator: "",
          sensors: {},
          status: TestStatus.DRAFT,
        },
  })

  const devices = watch("devices")
  const sensors = watch("sensors")
  const status = watch("status")
  const startDate = watch("start")
  const endDate = watch("end")

  return (
    <form onSubmit={handleSubmit(onSubmit)} className="space-y-6" noValidate>
      {/* Test ID */}
      <div className="space-y-2">
        <Label htmlFor="test_id">Test ID *</Label>
        <Input
          id="test_id"
          {...register("test_id")}
          disabled={!!initialData || isSubmitting}
          placeholder="Enter test ID"
        />
        {errors.test_id && (
          <p className="text-sm text-destructive">{errors.test_id.message}</p>
        )}
      </div>

      {/* Campaign ID */}
      <div className="space-y-2">
        <Label htmlFor="campaign_id">Campaign ID *</Label>
        <Input
          id="campaign_id"
          {...register("campaign_id")}
          disabled={isSubmitting}
          placeholder="Enter campaign ID"
        />
        {errors.campaign_id && (
          <p className="text-sm text-destructive">{errors.campaign_id.message}</p>
        )}
      </div>

      {/* Environment ID */}
      <div className="space-y-2">
        <Label htmlFor="environment_id">Environment ID *</Label>
        <Input
          id="environment_id"
          {...register("environment_id")}
          disabled={isSubmitting}
          placeholder="Enter Environment ID"
        />
        {errors.environment_id && (
          <p className="text-sm text-destructive">{errors.environment_id.message}</p>
        )}
      </div>

      {/* Operator */}
      <div className="space-y-2">
        <Label htmlFor="operator">Operator *</Label>
        <Input
          id="operator"
          {...register("operator")}
          disabled={isSubmitting}
          placeholder="Enter operator name"
        />
        {errors.operator && (
          <p className="text-sm text-destructive">{errors.operator.message}</p>
        )}
      </div>

      {/* Devices */}
      <div className="space-y-2">
        <Label>Devices (Devices Under Test) *</Label>
        <DevicePicker
          value={devices}
          onChange={(newDevices) => setValue("devices", newDevices, { shouldValidate: true })}
          error={errors.devices?.message}
        />
      </div>

      {/* Sensors (JSON Editor) */}
      <div className="space-y-2">
        <Label>Sensors Configuration *</Label>
        <JsonEditor
          value={JSON.stringify(sensors, null, 2)}
          onChange={(jsonString) => {
            try {
              const parsed = JSON.parse(jsonString)
              setValue("sensors", parsed, { shouldValidate: true })
            } catch {
              // Invalid JSON, don't update
            }
          }}
          readOnly={isSubmitting}
        />
        {errors.sensors && (
          <p className="text-sm text-destructive">{(errors.sensors as any).message as string}</p>
        )}
      </div>

      {/* Status */}
      <div className="space-y-2">
        <Label htmlFor="status">Status</Label>
        <Select
          value={status}
          onValueChange={(value) => setValue("status", value as TestStatus)}
          disabled={isSubmitting}
        >
          <SelectTrigger>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={TestStatus.DRAFT}>Draft</SelectItem>
            <SelectItem value={TestStatus.IN_PROGRESS}>In Progress</SelectItem>
            <SelectItem value={TestStatus.FINISHED}>Finished</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {/* Start/End Dates */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label htmlFor="start">Start Date (optional)</Label>
          <DateTimePicker
            value={startDate}
            onChange={(date) => setValue("start", date, { shouldValidate: true })}
            disabled={isSubmitting}
            placeholder="Pick start date and time"
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="end">End Date (optional)</Label>
          <DateTimePicker
            value={endDate}
            onChange={(date) => setValue("end", date, { shouldValidate: true })}
            disabled={isSubmitting}
            placeholder="Pick end date and time"
          />
        </div>
      </div>

      {/* Actions */}
      <div className="flex justify-end gap-4 pt-4">
        <Button
          type="button"
          variant="outline"
          onClick={onCancel}
          disabled={isSubmitting}
        >
          Cancel
        </Button>
        <Button type="submit" disabled={isSubmitting}>
          {isSubmitting ? "Saving..." : initialData ? "Update Test" : "Create Test"}
        </Button>
      </div>
    </form>
  )
}
