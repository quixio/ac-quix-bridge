/**
 * Zod validation schemas for Test forms
 */

import { z } from "zod"
import { TestStatus } from "@/types/test"

// Device Reference schema
export const deviceReferenceSchema = z.object({
  device_id: z.string().min(1, "Device ID is required"),
  device_version: z.string().nullable(),
})

// Test Create schema - matches backend TestCreate model
export const testCreateSchema = z.object({
  test_id: z.string().min(1, "Test ID is required"),
  campaign_id: z.string().min(1, "Campaign ID is required"),
  devices: z
    .array(deviceReferenceSchema)
    .min(1, "At least one Device is required"),
  environment_id: z.string().min(1, "Environment ID is required"),
  operator: z.string().min(1, "Operator is required"),
  sensors: z.record(z.record(z.any())).default({}),
  status: z.nativeEnum(TestStatus).default(TestStatus.DRAFT),
  start: z.date().optional(),
  end: z.date().optional(),
})

// Test Update schema - all fields optional except potentially changing ones
export const testUpdateSchema = z.object({
  campaign_id: z.string().min(1, "Campaign ID is required").optional(),
  devices: z.array(deviceReferenceSchema).min(1, "At least one Device is required").optional(),
  environment_id: z.string().min(1, "Environment ID is required").optional(),
  operator: z.string().min(1, "Operator is required").optional(),
  sensors: z.record(z.record(z.any())).optional(),
  status: z.nativeEnum(TestStatus).optional(),
  start: z.date().optional(),
  end: z.date().optional(),
})

// Type inference
export type TestCreateInput = z.infer<typeof testCreateSchema>
export type TestUpdateInput = z.infer<typeof testUpdateSchema>
