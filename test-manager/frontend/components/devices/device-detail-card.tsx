"use client"

import { DataCard } from "@/components/shared/data-card"
import { DeviceStatusBadge } from "./device-status-badge"
import { DeviceQRModal } from "./device-qr-modal"
import { Badge } from "@/components/ui/badge"
import type { Device } from "@/types/device"
import { useDateFormatter } from "@/lib/hooks/use-date-formatter"

interface DeviceDetailCardProps {
  device: Device
}

export function DeviceDetailCard({ device }: DeviceDetailCardProps) {
  const { formatDateTime } = useDateFormatter()

  return (
    <div className="space-y-6">
      {/* General Information */}
      <DataCard
        title="General Information"
        headerAction={
          <DeviceQRModal
            deviceId={device.device_id}
            productName={`${device.manufacturer} ${device.product_name}`}
          />
        }
        items={[
          { label: "Device ID", value: device.device_id },
          { label: "Status", value: <DeviceStatusBadge status={device.status} /> },
          { label: "Status Note", value: device.status_note || "N/A" },
          { label: "Sample ID", value: device.sample_id },
          { label: "Sample Type", value: device.sample_type },
          { label: "Sample Number", value: device.sample_nr || "N/A" },
        ]}
      />

      {/* Product Information */}
      <DataCard
        title="Product Information"
        items={[
          { label: "Manufacturer", value: device.manufacturer },
          { label: "Product Category", value: device.product_category },
          { label: "Product Name", value: device.product_name },
          { label: "Product Type", value: device.product_type || "N/A" },
          { label: "Product Variant", value: device.product_variant || "N/A" },
          { label: "Product Key", value: device.product_key || "N/A" },
        ]}
      />

      {/* Organization & Location */}
      <DataCard
        title="Organization & Location"
        items={[
          { label: "Location", value: device.location },
          { label: "Sample Owner", value: device.sample_owner || "N/A" },
          { label: "Project", value: device.project || "N/A" },
          {
            label: "Picture Link",
            value: device.picture_link ? (
              <a
                href={device.picture_link}
                target="_blank"
                rel="noopener noreferrer"
                className="text-primary hover:underline"
              >
                View Pictures
              </a>
            ) : (
              "N/A"
            ),
          },
        ]}
      />

      {/* Metadata & References */}
      <DataCard
        title="Metadata & References"
        items={[
          {
            label: "Teamcenter Serial",
            value: device.teamcenter_serial_nr || "N/A",
          },
          { label: "Software Bundle", value: device.software_bundle || "N/A" },
          {
            label: "Hardware Link",
            value: device.hardware_link ? (
              <a
                href={device.hardware_link}
                target="_blank"
                rel="noopener noreferrer"
                className="text-primary hover:underline"
              >
                View Hardware
              </a>
            ) : (
              "N/A"
            ),
          },
          { label: "Comment", value: device.comment || "N/A" },
        ]}
      />

      {/* Safety Operations */}
      <DataCard
        title="Safety Operations"
        items={[
          {
            label: "Attended Operation",
            value: device.attended_operation ? (
              <Badge variant="outline" className="bg-green-500/10 text-green-600 border-green-500/20">
                Approved
              </Badge>
            ) : (
              <Badge variant="outline" className="bg-yellow-500/10 text-yellow-600 border-yellow-500/20">
                Not Approved
              </Badge>
            ),
          },
          {
            label: "Unattended Operation",
            value: device.unattended_operation ? (
              <Badge variant="outline" className="bg-green-500/10 text-green-600 border-green-500/20">
                Approved
              </Badge>
            ) : (
              <Badge variant="outline" className="bg-yellow-500/10 text-yellow-600 border-yellow-500/20">
                Not Approved
              </Badge>
            ),
          },
        ]}
      />

      {/* Audit Trail */}
      <DataCard
        title="Audit Trail"
        items={[
          {
            label: "Created",
            value: formatDateTime(device.created_at),
          },
          {
            label: "Updated",
            value: formatDateTime(device.updated_at),
          },
          { label: "Creator", value: device.creator },
          { label: "Last Editor", value: device.last_editor },
        ]}
      />
    </div>
  )
}
