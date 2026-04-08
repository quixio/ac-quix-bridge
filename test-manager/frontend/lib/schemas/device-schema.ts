/**
 * Zod validation schemas for Device forms
 */

import { z } from "zod"
import { DeviceStatus, JournalCategory } from "@/types/device"

/**
 * Device Create schema
 * Used for creating new Devices
 */
export const deviceCreateSchema = z.object({
  // Device Identification
  device_id: z.string().min(1, "Device ID is required"),
  creator: z.string().min(1, "Creator is required"),

  // Product Information
  manufacturer: z.string().min(1, "Manufacturer is required"),
  product_category: z.string().min(1, "Product category is required"),
  product_name: z.string().min(1, "Product name is required"),
  product_type: z.string().optional().nullable(),
  product_variant: z.string().optional().nullable(),
  product_key: z.string().optional().nullable(),

  // Sample Information
  sample_type: z.string().min(1, "Sample type is required"),
  sample_nr: z.string().optional().nullable(),

  // Status
  status: z.nativeEnum(DeviceStatus).default(DeviceStatus.CREATED),
  status_note: z.string().optional().nullable(),

  // Organization
  location: z.string().min(1, "Location is required"),
  sample_owner: z.string().optional().nullable(),
  project: z.string().optional().nullable(),
  picture_link: z.string().url("Must be a valid URL").optional().nullable().or(z.literal("")),

  // Metadata
  software_bundle: z.string().optional().nullable(),
  hardware_link: z.string().optional().nullable(),
  comment: z.string().optional().nullable(),

  // Journal (optional for create)
  journal_category: z.nativeEnum(JournalCategory).optional().nullable(),
  journal_text: z.string().optional().nullable(),
})

/**
 * Device Update schema
 * Used for editing existing Devices
 */
export const deviceUpdateSchema = z.object({
  // Product Information (all editable per updated domain model)
  manufacturer: z.string().min(1, "Manufacturer is required").optional(),
  product_category: z.string().min(1, "Product category is required").optional(),
  product_name: z.string().min(1, "Product name is required").optional(),
  product_type: z.string().optional().nullable(),
  product_variant: z.string().optional().nullable(),
  product_key: z.string().optional().nullable(),

  // Sample Information (all editable per updated domain model)
  sample_type: z.string().min(1, "Sample type is required").optional(),
  sample_nr: z.string().optional().nullable(),

  // Status
  status: z.nativeEnum(DeviceStatus).optional(),
  status_note: z.string().optional().nullable(),

  // Organization
  location: z.string().min(1, "Location is required").optional(),
  sample_owner: z.string().optional().nullable(),
  project: z.string().optional().nullable(),
  picture_link: z.string().url("Must be a valid URL").optional().nullable().or(z.literal("")),

  // Metadata
  software_bundle: z.string().optional().nullable(),
  hardware_link: z.string().optional().nullable(),
  comment: z.string().optional().nullable(),

  // Journal text (optional - provided in preview dialog for edit flow)
  journal_text: z.string().optional(),

  // Audit
  last_editor: z.string().optional(),
})

/**
 * Manual journal entry creation schema
 */
export const journalEntryCreateSchema = z.object({
  category: z.nativeEnum(JournalCategory).optional().nullable(),
  text: z.string().min(1, "Journal text is required"),
})

/**
 * Type inference from schemas
 */
export type DeviceCreateFormData = z.infer<typeof deviceCreateSchema>
export type DeviceUpdateFormData = z.infer<typeof deviceUpdateSchema>
export type JournalEntryCreateFormData = z.infer<typeof journalEntryCreateSchema>

/**
 * Helper to derive sample_id from sample_type and sample_nr
 */
export function deriveSampleId(sampleType: string, sampleNr?: string | null): string {
  if (sampleNr && sampleNr.trim()) {
    return `${sampleType}-${sampleNr.trim()}`
  }
  return sampleType
}
