/**
 * Utility functions for Device (Device Under Test) entity
 */

import {
  DeviceStatus,
  DeviceStatusLabels,
  DeviceStatusColors,
  JournalCategory,
} from "@/types/device"

/**
 * Format sample ID from sample_type and sample_nr
 * Follows backend logic: {sample_type} or {sample_type}-{sample_nr}
 */
export function formatSampleId(
  sampleType: string,
  sampleNr?: string | null
): string {
  if (sampleNr) {
    return `${sampleType}-${sampleNr}`
  }
  return sampleType
}

/**
 * Get human-readable label for Device status
 */
export function formatDeviceStatus(status: DeviceStatus): string {
  return DeviceStatusLabels[status] || status
}

/**
 * Get color for Device status badge
 */
export function getDeviceStatusColor(status: DeviceStatus): string {
  return DeviceStatusColors[status] || "gray"
}

/**
 * Format journal category for display
 */
export function formatJournalCategory(
  category: JournalCategory | null
): string {
  return category || "General"
}

/**
 * Get journal category color key for CSS variables
 */
export function getJournalCategoryColor(
  category: JournalCategory | null
): "safety" | "setup" | "testing" | "location" | "hw" | "sw" | null {
  if (!category) return null

  const colorMap: Record<JournalCategory, "safety" | "setup" | "testing" | "location" | "hw" | "sw"> = {
    [JournalCategory.SAFETY_REQUIREMENTS]: "safety",
    [JournalCategory.SETUP]: "setup",
    [JournalCategory.TESTING]: "testing",
    [JournalCategory.CHANGE_LOCATION]: "location",
    [JournalCategory.HW_MODIFICATION]: "hw",
    [JournalCategory.SW_MODIFICATION]: "sw",
  }

  return colorMap[category] || null
}

/**
 * Get Tailwind classes for journal category badge
 */
export function getJournalCategoryClasses(
  category: JournalCategory | null
): string {
  const colorKey = getJournalCategoryColor(category)

  if (!colorKey) {
    return "bg-muted/30 text-foreground border-border"
  }

  const classMap = {
    safety: "bg-destructive/10 text-destructive border-destructive/20",
    setup: "bg-success/10 text-success border-success/20",
    testing: "bg-journal-testing/10 text-journal-testing border-journal-testing/20",
    location: "bg-journal-location/10 text-journal-location border-journal-location/20",
    hw: "bg-warning/10 text-warning border-warning/20",
    sw: "bg-journal-sw/10 text-journal-sw border-journal-sw/20",
  }

  return classMap[colorKey]
}
