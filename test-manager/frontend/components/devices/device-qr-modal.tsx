"use client"

import { useState } from "react"
import { QRCodeSVG } from "qrcode.react"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { Printer, Download, Copy, QrCode } from "lucide-react"
import { useToast } from "@/lib/hooks/use-toast"

interface DeviceQRModalProps {
  deviceId: string
  productName?: string
  /** Render as icon-only button instead of full button */
  variant?: "button" | "icon"
}

export function DeviceQRModal({ deviceId, productName, variant = "button" }: DeviceQRModalProps) {
  const [isOpen, setIsOpen] = useState(false)
  const { toast } = useToast()
  const [deviceUrl, setDeviceUrl] = useState<string>("")

  // Generate URL when modal opens (client-side only)
  const handleOpen = () => {
    if (typeof window !== "undefined") {
      setDeviceUrl(`${window.location.origin}/devices/${deviceId}`)
    }
    setIsOpen(true)
  }

  const handlePrint = () => {
    window.print()
  }

  const handleDownload = () => {
    const svg = document.getElementById("qr-code-svg")
    if (!svg) return

    // Create canvas from SVG
    const canvas = document.createElement("canvas")
    const ctx = canvas.getContext("2d")
    const svgData = new XMLSerializer().serializeToString(svg)
    const img = new Image()

    img.onload = () => {
      canvas.width = img.width
      canvas.height = img.height
      ctx?.drawImage(img, 0, 0)

      // Download as PNG
      canvas.toBlob((blob) => {
        if (!blob) return
        const url = URL.createObjectURL(blob)
        const a = document.createElement("a")
        a.href = url
        a.download = `qr-${deviceId}.png`
        a.click()
        URL.revokeObjectURL(url)
      })
    }

    img.src = "data:image/svg+xml;base64," + btoa(svgData)
  }

  const handleCopyLink = async () => {
    try {
      await navigator.clipboard.writeText(deviceUrl)
      toast({
        title: "Link copied",
        description: "Device URL copied to clipboard",
      })
    } catch (error) {
      toast({
        title: "Copy failed",
        description: "Failed to copy link to clipboard",
        variant: "destructive",
      })
    }
  }

  return (
    <>
      {/* Trigger Button */}
      {variant === "icon" ? (
        <button
          onClick={handleOpen}
          className="inline-flex items-center justify-center text-muted-foreground hover:text-foreground transition-colors"
          title="View QR Code"
        >
          <QrCode className="h-5 w-5" />
        </button>
      ) : (
        <Button variant="outline" size="sm" onClick={handleOpen}>
          <QrCode className="h-4 w-4 mr-2" />
          QR Code
        </Button>
      )}

      {/* Modal */}
      <Dialog open={isOpen} onOpenChange={setIsOpen}>
        <DialogContent className="sm:max-w-md print-content">
          <DialogHeader className="print:hidden">
            <DialogTitle>QR Code - {deviceId}</DialogTitle>
          </DialogHeader>

          <div id="qr-print-area" className="flex flex-col items-center space-y-4 print:space-y-6 print:py-8">
            {/* QR Code */}
            <div className="p-4 bg-white rounded-lg border-2 border-gray-200 print:border-4 print:p-6 print:rounded-none">
              {deviceUrl && (
                <QRCodeSVG
                  id="qr-code-svg"
                  value={deviceUrl}
                  size={256}
                  level="M"
                  includeMargin={true}
                />
              )}
            </div>

            {/* Device Information */}
            <div className="text-center space-y-1 print:space-y-3">
              <p className="font-bold text-xl print:text-4xl print:font-bold">{deviceId}</p>
              {productName && (
                <p className="text-sm text-muted-foreground print:text-2xl print:text-black">
                  {productName}
                </p>
              )}
              <p className="text-xs text-muted-foreground print:text-lg print:text-black print:mt-4 print:break-all">
                {deviceUrl}
              </p>
            </div>

            {/* Action Buttons - Hidden when printing */}
            <div className="flex flex-wrap gap-2 justify-center print:hidden">
              <Button onClick={handlePrint} variant="default">
                <Printer className="h-4 w-4 mr-2" />
                Print Label
              </Button>
              <Button onClick={handleDownload} variant="outline">
                <Download className="h-4 w-4 mr-2" />
                Download PNG
              </Button>
              <Button onClick={handleCopyLink} variant="outline">
                <Copy className="h-4 w-4 mr-2" />
                Copy Link
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Print-specific styles */}
      <style jsx global>{`
        @media print {
          @page {
            size: A4;
            margin: 1.5cm;
          }

          /* Hide everything */
          * {
            visibility: hidden !important;
          }

          /* Show only QR print area and descendants */
          #qr-print-area,
          #qr-print-area * {
            visibility: visible !important;
          }

          /* Reset and center QR print area */
          #qr-print-area {
            position: absolute !important;
            left: 50% !important;
            top: 50% !important;
            transform: translate(-50%, -50%) !important;
            width: auto !important;
            height: auto !important;
            display: flex !important;
            flex-direction: column !important;
            align-items: center !important;
            justify-content: center !important;
            margin: 0 !important;
            padding: 0 !important;
          }

          /* QR code container */
          #qr-print-area > div:first-child {
            margin-bottom: 1.5cm !important;
            padding: 0 !important;
            border: 4px solid #333 !important;
            background: white !important;
          }

          /* QR code sizing */
          #qr-code-svg {
            width: 8cm !important;
            height: 8cm !important;
            display: block !important;
          }

          /* Text container */
          #qr-print-area > div:last-of-type {
            text-align: center !important;
            max-width: 15cm !important;
          }

          /* Text styling */
          #qr-print-area p {
            margin: 0.4cm 0 !important;
            color: black !important;
            page-break-inside: avoid !important;
          }

          /* Device ID */
          #qr-print-area p:first-child {
            font-size: 28pt !important;
            font-weight: bold !important;
            margin-bottom: 0.3cm !important;
          }

          /* Product name */
          #qr-print-area p:nth-child(2) {
            font-size: 16pt !important;
            margin-bottom: 0.5cm !important;
          }

          /* URL */
          #qr-print-area p:last-child {
            font-size: 11pt !important;
            word-break: break-all !important;
            line-height: 1.3 !important;
          }
        }
      `}</style>
    </>
  )
}
