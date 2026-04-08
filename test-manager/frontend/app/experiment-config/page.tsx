"use client"

import { MainLayout } from "@/components/layout/main-layout"
import { Loader2 } from "lucide-react"
import { useState } from "react"

export default function ExperimentConfigPage() {
  const [loading, setLoading] = useState(true)
  const configFormUrl = process.env.NEXT_PUBLIC_CONFIG_FORM_URL || "http://localhost:8002"

  return (
    <MainLayout noPadding>
      {loading && (
        <div className="flex items-center justify-center min-h-[500px]">
          <div className="flex flex-col items-center gap-4">
            <Loader2 className="h-8 w-8 animate-spin text-primary" />
            <p className="text-muted-foreground">Loading Experiment Config...</p>
          </div>
        </div>
      )}
      <iframe
        src={configFormUrl}
        className="w-full h-[calc(100vh-4rem)] border-0"
        style={{ display: loading ? "none" : "block" }}
        title="Experiment Configuration"
        onLoad={() => setLoading(false)}
      />
    </MainLayout>
  )
}
