/**
 * Device Form Component
 * Comprehensive form for creating and editing Devices with conditional validation
 */

"use client"

import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { useEffect, useMemo, useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Separator } from "@/components/ui/separator"
import { Loader2, Info } from "lucide-react"
import {
  useSampleTypes,
  useLocations,
  useProductCategories,
  useProducts,
} from "@/lib/hooks/use-lookups"
import {
  deviceCreateSchema,
  deviceUpdateSchema,
  deriveSampleId,
  type DeviceCreateFormData,
  type DeviceUpdateFormData,
} from "@/lib/schemas/device-schema"
import { DeviceStatus, JournalCategory, type Device } from "@/types/device"

interface DeviceFormProps {
  mode: "create" | "edit"
  device?: Device
  onSubmit: (data: DeviceCreateFormData | DeviceUpdateFormData) => Promise<void>
  onCancel: () => void
  currentUser?: string
}

export function DeviceForm({ mode, device, onSubmit, onCancel, currentUser }: DeviceFormProps) {
  const isEditMode = mode === "edit"

  // Form state with appropriate schema
  const form = useForm<DeviceCreateFormData | DeviceUpdateFormData>({
    resolver: zodResolver(isEditMode ? deviceUpdateSchema : deviceCreateSchema),
    defaultValues: isEditMode && device
      ? {
          manufacturer: device.manufacturer,
          product_category: device.product_category,
          product_name: device.product_name,
          product_type: device.product_type || "",
          product_variant: device.product_variant || "",
          product_key: device.product_key || "",
          sample_type: device.sample_type,
          sample_nr: device.sample_nr || "",
          status: device.status,
          status_note: device.status_note || "",
          location: device.location,
          sample_owner: device.sample_owner || "",
          project: device.project || "",
          picture_link: device.picture_link || "",
          software_bundle: device.software_bundle || "",
          hardware_link: device.hardware_link || "",
          comment: device.comment || "",
          last_editor: currentUser || "",
        }
      : {
          device_id: "",
          creator: currentUser || "",
          manufacturer: "",
          product_category: "",
          product_name: "",
          product_type: "",
          product_variant: "",
          product_key: "",
          sample_type: "",
          sample_nr: "",
          status: DeviceStatus.CREATED,
          status_note: "",
          location: "",
          sample_owner: "",
          project: "",
          picture_link: "",
          software_bundle: "",
          hardware_link: "",
          comment: "",
          journal_category: JournalCategory.SETUP,
          journal_text: "Device created",
        },
  })

  // Load lookups
  const { sampleTypes, loading: loadingSampleTypes } = useSampleTypes()
  const { locations, loading: loadingLocations } = useLocations()
  const { categories, loading: loadingCategories } = useProductCategories()

  // Watch form fields for cascading dropdowns and conditional logic
  const watchedManufacturer = form.watch("manufacturer")
  const watchedProductCategory = form.watch("product_category")
  const watchedSampleType = form.watch("sample_type")
  const watchedSampleNr = form.watch("sample_nr")

  // Fetch all products (no filter) to get manufacturers list
  const { products: allProducts, loading: loadingAllProducts } = useProducts()

  // Fetch products based on selected manufacturer and category
  const { products, loading: loadingProducts } = useProducts(
    watchedManufacturer || undefined,
    watchedProductCategory || undefined
  )

  // Get unique manufacturers from all products
  const manufacturers = useMemo(() => {
    if (!allProducts || allProducts.length === 0) return []
    return Array.from(new Set(allProducts.map((p) => p.manufacturer))).sort()
  }, [allProducts])

  // Derived sample ID preview
  const sampleIdPreview = useMemo(() => {
    if (!watchedSampleType) return ""
    return deriveSampleId(watchedSampleType, watchedSampleNr)
  }, [watchedSampleType, watchedSampleNr])

  // Reset dependent fields when parent changes
  useEffect(() => {
    if (isEditMode) return // Don't reset in edit mode
    form.setValue("product_category", "")
    form.setValue("product_name", "")
  }, [watchedManufacturer, form, isEditMode])

  useEffect(() => {
    if (isEditMode) return
    form.setValue("product_name", "")
  }, [watchedProductCategory, form, isEditMode])

  // Handle form submission
  const handleSubmit = async (data: DeviceCreateFormData | DeviceUpdateFormData) => {
    try {
      await onSubmit(data)
    } catch (error) {
      console.error("Form submission error:", error)
    }
  }

  const isLoading = loadingSampleTypes || loadingLocations || loadingCategories || loadingProducts || loadingAllProducts

  return (
    <form onSubmit={form.handleSubmit(handleSubmit)} className="space-y-6">
      {/* Device Identification Section (Create mode only, or read-only in edit mode) */}
      {!isEditMode && (
        <Card>
          <CardHeader>
            <CardTitle>Device Identification</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div>
              <Label htmlFor="device_id">Device ID *</Label>
              <Input
                id="device_id"
                {...form.register("device_id" as any)}
                placeholder="Enter unique Device identifier"
              />
              {(form.formState.errors as any).device_id && (
                <p className="text-sm text-red-500 mt-1">{(form.formState.errors as any).device_id.message as string}</p>
              )}
            </div>

            <div>
              <Label htmlFor="creator">Creator *</Label>
              <Input
                id="creator"
                {...form.register("creator" as any)}
                placeholder="Your name"
                disabled={!!currentUser}
              />
              {(form.formState.errors as any).creator && (
                <p className="text-sm text-red-500 mt-1">{(form.formState.errors as any).creator.message as string}</p>
              )}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Product Information */}
      <Card>
        <CardHeader>
          <CardTitle>Product Information</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div>
            <Label htmlFor="manufacturer">Manufacturer *</Label>
            <Select
              value={form.watch("manufacturer") || ""}
              onValueChange={(value) => form.setValue("manufacturer", value)}
            >
              <SelectTrigger id="manufacturer">
                <SelectValue placeholder="Select manufacturer" />
              </SelectTrigger>
              <SelectContent>
                {manufacturers.map((mfr) => (
                  <SelectItem key={mfr} value={mfr}>
                    {mfr}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {form.formState.errors.manufacturer && (
              <p className="text-sm text-red-500 mt-1">{form.formState.errors.manufacturer.message as string}</p>
            )}
          </div>

          <div>
            <Label htmlFor="product_category">Product Category *</Label>
            <Select
              value={form.watch("product_category") || ""}
              onValueChange={(value) => form.setValue("product_category", value)}
              disabled={!watchedManufacturer}
            >
              <SelectTrigger id="product_category">
                <SelectValue placeholder="Select product category" />
              </SelectTrigger>
              <SelectContent>
                {categories.map((cat) => (
                  <SelectItem key={cat._id} value={cat._id}>
                    {cat.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {form.formState.errors.product_category && (
              <p className="text-sm text-red-500 mt-1">{form.formState.errors.product_category.message as string}</p>
            )}
          </div>

          <div>
            <Label htmlFor="product_name">Product Name *</Label>
            <Select
              value={form.watch("product_name") || ""}
              onValueChange={(value) => form.setValue("product_name", value)}
              disabled={!watchedProductCategory || loadingProducts}
            >
              <SelectTrigger id="product_name">
                <SelectValue placeholder="Select product name" />
              </SelectTrigger>
              <SelectContent>
                {products.map((prod) => (
                  <SelectItem key={prod._id} value={prod.product_name}>
                    {prod.product_name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {form.formState.errors.product_name && (
              <p className="text-sm text-red-500 mt-1">{form.formState.errors.product_name.message as string}</p>
            )}
          </div>

          <div className="grid grid-cols-3 gap-4">
            <div>
              <Label htmlFor="product_type">Product Type</Label>
              <Input
                id="product_type"
                {...form.register("product_type" as any)}
                placeholder="Optional"
              />
            </div>
            <div>
              <Label htmlFor="product_variant">Product Variant</Label>
              <Input
                id="product_variant"
                {...form.register("product_variant" as any)}
                placeholder="Optional"
              />
            </div>
            <div>
              <Label htmlFor="product_key">Product Key</Label>
              <Input
                id="product_key"
                {...form.register("product_key" as any)}
                placeholder="Optional"
              />
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Sample Information */}
      <Card>
        <CardHeader>
          <CardTitle>Sample Information</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div>
              <Label htmlFor="sample_type">Sample Type *</Label>
              <Select
                value={form.watch("sample_type") || ""}
                onValueChange={(value) => form.setValue("sample_type", value)}
                disabled={loadingSampleTypes}
              >
                <SelectTrigger id="sample_type">
                  <SelectValue placeholder="Select sample type" />
                </SelectTrigger>
                <SelectContent>
                  {sampleTypes.map((st) => (
                    <SelectItem key={st._id} value={st.sample_type}>
                      {st.sample_type}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {form.formState.errors.sample_type && (
                <p className="text-sm text-red-500 mt-1">{form.formState.errors.sample_type.message as string}</p>
              )}
            </div>

            <div>
              <Label htmlFor="sample_nr">Sample Number</Label>
              <Input
                id="sample_nr"
                {...form.register("sample_nr" as any)}
                placeholder="Optional"
              />
            </div>
          </div>

          {/* Sample ID Preview */}
          {sampleIdPreview && (
            <div className="p-3 bg-blue-50 dark:bg-blue-900/20 rounded-md flex items-start gap-2">
              <Info className="h-5 w-5 text-blue-500 mt-0.5" />
              <div>
                <p className="text-sm font-medium text-blue-900 dark:text-blue-100">
                  Sample ID Preview
                </p>
                <p className="text-sm text-blue-700 dark:text-blue-300 font-mono">
                  {sampleIdPreview}
                </p>
              </div>
            </div>
          )}

          <div>
            <Label htmlFor="teamcenter_serial_nr">Teamcenter Serial Number</Label>
            <Input
              id="teamcenter_serial_nr"
              {...form.register("teamcenter_serial_nr" as any)}
              placeholder="Optional"
            />
          </div>
        </CardContent>
      </Card>

      {/* Status & Organization */}
      <Card>
        <CardHeader>
          <CardTitle>Status & Organization</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div>
              <Label htmlFor="status">Status *</Label>
              <Select
                value={form.watch("status") || DeviceStatus.CREATED}
                onValueChange={(value) => form.setValue("status", value as DeviceStatus)}
              >
                <SelectTrigger id="status">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {Object.values(DeviceStatus).map((status) => (
                    <SelectItem key={status} value={status}>
                      {status.charAt(0).toUpperCase() + status.slice(1)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div>
              <Label htmlFor="location">Location *</Label>
              <Select
                value={form.watch("location") || ""}
                onValueChange={(value) => form.setValue("location", value)}
                disabled={loadingLocations}
              >
                <SelectTrigger id="location">
                  <SelectValue placeholder="Select location" />
                </SelectTrigger>
                <SelectContent>
                  {locations.map((loc) => (
                    <SelectItem key={loc._id} value={loc.location}>
                      {loc.location}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {form.formState.errors.location && (
                <p className="text-sm text-red-500 mt-1">{form.formState.errors.location.message as string}</p>
              )}
            </div>
          </div>

          <div>
            <Label htmlFor="status_note">Status Note</Label>
            <Textarea
              id="status_note"
              {...form.register("status_note" as any)}
              placeholder="Optional notes about the status"
              rows={2}
            />
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <Label htmlFor="sample_owner">Sample Owner</Label>
              <Input
                id="sample_owner"
                {...form.register("sample_owner" as any)}
                placeholder="Person or team responsible"
              />
            </div>

            <div>
              <Label htmlFor="project">Project</Label>
              <Input
                id="project"
                {...form.register("project" as any)}
                placeholder="Project name"
              />
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Additional Information */}
      <Card>
        <CardHeader>
          <CardTitle>Additional Information</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div>
            <Label htmlFor="software_bundle">Software Bundle</Label>
            <Input
              id="software_bundle"
              {...form.register("software_bundle" as any)}
              placeholder="SW package/build identifier"
            />
          </div>

          <div>
            <Label htmlFor="hardware_link">Hardware Link</Label>
            <Input
              id="hardware_link"
              {...form.register("hardware_link" as any)}
              placeholder="Link/ID to hardware asset"
            />
          </div>

          <div>
            <Label htmlFor="picture_link">Picture Link</Label>
            <Input
              id="picture_link"
              type="url"
              {...form.register("picture_link" as any)}
              placeholder="https://..."
            />
            {form.formState.errors.picture_link && (
              <p className="text-sm text-red-500 mt-1">{form.formState.errors.picture_link.message as string}</p>
            )}
          </div>

          <div>
            <Label htmlFor="comment">Comment</Label>
            <Textarea
              id="comment"
              {...form.register("comment" as any)}
              placeholder="General notes"
              rows={3}
            />
          </div>
        </CardContent>
      </Card>

      {/* Journal Entry - Removed from edit mode (handled in preview dialog) */}

      {/* Form Actions */}
      <div className="flex justify-end gap-4">
        <Button type="button" variant="outline" onClick={onCancel}>
          Cancel
        </Button>
        <Button type="submit" disabled={form.formState.isSubmitting || isLoading}>
          {form.formState.isSubmitting && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
          {isEditMode ? "Update Device" : "Create Device"}
        </Button>
      </div>
    </form>
  )
}
