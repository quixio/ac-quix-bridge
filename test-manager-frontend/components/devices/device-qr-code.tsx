"use client";

import { QRCodeSVG } from "qrcode.react";
import { Card } from "@/components/ui/card";

interface DeviceQRCodeProps {
  deviceId: string;
  size?: number;
  title?: string;
}

/**
 * QR Code component for Device detail page.
 * Generates a scannable QR code that links to the Device detail page.
 */
export function DeviceQRCode({ deviceId, size = 150, title = "Scan to view Device" }: DeviceQRCodeProps) {
  // Generate the full URL for the Device detail page
  const deviceUrl = typeof window !== "undefined"
    ? `${window.location.origin}/devices/${deviceId}`
    : "";

  if (!deviceUrl) {
    return null;
  }

  return (
    <Card className="p-4">
      <div className="flex flex-col items-center gap-2">
        {title && (
          <h3 className="text-sm font-semibold text-center">{title}</h3>
        )}
        <QRCodeSVG
          value={deviceUrl}
          size={size}
          level="L"
          includeMargin={true}
          className="border border-border rounded"
        />
      </div>
    </Card>
  );
}
