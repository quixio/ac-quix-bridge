"use client"

import { Suspense } from "react"
import { useSearchParams, useRouter } from "next/navigation"
import { MainLayout } from "@/components/layout/main-layout"
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs"
import { Card, CardContent } from "@/components/ui/card"
import {
  GitCompare,
  MapPin,
  Radio,
  BarChart3,
  Trophy,
  BookOpenText,
} from "lucide-react"

const ANALYSIS_TABS = [
  {
    value: "compare",
    label: "Compare",
    icon: GitCompare,
    title: "Compare Runs",
    description:
      "Compare laps across multiple tests to find performance tradeoffs. Overlay speed, tire temperatures, and driver inputs by track position.",
  },
  {
    value: "per-corner",
    label: "Per-Corner",
    icon: MapPin,
    title: "Per-Corner Analysis",
    description:
      "Analyze performance at specific track corners using sector metadata. See entry speed, min speed, exit speed, and time-in-corner for each run.",
  },
  {
    value: "live",
    label: "Live",
    icon: Radio,
    title: "Live Telemetry",
    description:
      "Real-time telemetry dashboard for tests currently in progress. Monitor speed, tire temps, lap splits, and driver inputs as they happen.",
  },
  {
    value: "single-run",
    label: "Single Run",
    icon: BarChart3,
    title: "Single Run Analysis",
    description:
      "Deep-dive analysis of a single test. Lap-by-lap breakdown, corner-by-corner performance, driver input traces, and telemetry small-multiples.",
  },
  {
    value: "leaderboard",
    label: "Leaderboard",
    icon: Trophy,
    title: "Leaderboard",
    description:
      "Historical best laps with real-time ghost projection. Track your fastest laps across sessions and see a live projected lap time during active tests.",
  },
  {
    value: "notebook",
    label: "Notebook",
    icon: BookOpenText,
    title: "Interactive Notebook",
    description:
      "Interactive data science notebook for advanced analysis. Currently available via Analytics in the sidebar.",
  },
] as const

function AnalysisPageContent() {
  const router = useRouter()
  const searchParams = useSearchParams()

  const activeTab = searchParams.get("tab") || "compare"
  const testIds =
    searchParams.get("test_ids")?.split(",").filter(Boolean) || []
  const corner = searchParams.get("corner") || null

  const handleTabChange = (value: string) => {
    const params = new URLSearchParams(searchParams.toString())
    params.set("tab", value)
    router.push(`/analysis?${params.toString()}`)
  }

  return (
    <MainLayout>
      <div className="max-w-7xl">
        <div className="mb-6">
          <h1 className="text-3xl font-bold tracking-tight">Analysis</h1>
          <p className="text-muted-foreground">
            Performance analysis tools for test data
          </p>
        </div>

        {(testIds.length > 0 || corner) && (
          <div className="mb-4 flex gap-2 text-sm">
            {testIds.length > 0 && (
              <span className="rounded-md bg-muted px-2 py-1 text-muted-foreground">
                Tests: {testIds.join(", ")}
              </span>
            )}
            {corner && (
              <span className="rounded-md bg-muted px-2 py-1 text-muted-foreground">
                Corner: {corner}
              </span>
            )}
          </div>
        )}

        <Tabs value={activeTab} onValueChange={handleTabChange}>
          <TabsList>
            {ANALYSIS_TABS.map((tab) => (
              <TabsTrigger key={tab.value} value={tab.value}>
                <tab.icon className="mr-2 h-4 w-4" />
                {tab.label}
              </TabsTrigger>
            ))}
          </TabsList>

          {ANALYSIS_TABS.map((tab) => (
            <TabsContent key={tab.value} value={tab.value}>
              <Card>
                <CardContent className="flex flex-col items-center justify-center py-16 text-center">
                  <div className="mb-4 rounded-full bg-primary/10 p-4">
                    <tab.icon className="h-8 w-8 text-primary" />
                  </div>
                  <h2 className="text-xl font-semibold mb-2">{tab.title}</h2>
                  <p className="text-sm text-muted-foreground max-w-md mb-4">
                    {tab.description}
                  </p>
                  <span className="text-xs font-medium text-muted-foreground bg-muted px-3 py-1 rounded-full">
                    Coming soon
                  </span>
                </CardContent>
              </Card>
            </TabsContent>
          ))}
        </Tabs>
      </div>
    </MainLayout>
  )
}

export default function AnalysisPage() {
  return (
    <Suspense
      fallback={
        <MainLayout>
          <div className="flex items-center justify-center min-h-[500px]">
            <p className="text-muted-foreground">Loading...</p>
          </div>
        </MainLayout>
      }
    >
      <AnalysisPageContent />
    </Suspense>
  )
}
