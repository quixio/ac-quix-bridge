"use client"

import { useEffect, useMemo, useState } from "react"
import { useRouter } from "next/navigation"
import { ApiError } from "@/lib/api/client"
import { useLeaderboard } from "@/lib/hooks/use-leaderboard"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { EmptyState } from "@/components/shared/empty-state"
import { Trophy } from "lucide-react"
import { LeaderboardTable, type RankedLap } from "./leaderboard-table"

/**
 * Leaderboard tab — one fetch on mount, three dropdowns (Track / Car /
 * Experiment), and a flat per-driver best-lap table filtered client-side.
 *
 * Filter options come from the distinct partition values present in the
 * payload, so experiments with zero valid laps never appear in the
 * dropdown (this is an intentional V1 trade-off, see spec §5.6 / Q1).
 */
export function LeaderboardTab() {
  const router = useRouter()
  const { data, loading, error, refetch, tracks, cars, experiments } =
    useLeaderboard()

  const [selectedTrack, setSelectedTrack] = useState<string | null>(null)
  const [selectedCar, setSelectedCar] = useState<string | null>(null)
  const [selectedExperiment, setSelectedExperiment] = useState<string | null>(
    null
  )

  // Auto-select the first alphabetical value in each dropdown once the
  // payload arrives. Reselect only if the current selection is absent
  // from the new option list (e.g. after a refetch that removed it).
  useEffect(() => {
    if (tracks.length && (selectedTrack === null || !tracks.includes(selectedTrack))) {
      setSelectedTrack(tracks[0])
    }
  }, [tracks, selectedTrack])

  useEffect(() => {
    if (cars.length && (selectedCar === null || !cars.includes(selectedCar))) {
      setSelectedCar(cars[0])
    }
  }, [cars, selectedCar])

  useEffect(() => {
    if (
      experiments.length &&
      (selectedExperiment === null || !experiments.includes(selectedExperiment))
    ) {
      setSelectedExperiment(experiments[0])
    }
  }, [experiments, selectedExperiment])

  // Filter + sort + rank in one memo so the table gets a stable row
  // identity whenever any of the three selections change.
  const rankedRows = useMemo<RankedLap[]>(() => {
    if (!selectedTrack || !selectedCar || !selectedExperiment) return []
    const filtered = data.filter(
      (r) =>
        r.track === selectedTrack &&
        r.car === selectedCar &&
        r.experiment === selectedExperiment
    )
    const sorted = [...filtered].sort((a, b) => a.best_lap_ms - b.best_lap_ms)
    return sorted.map((r, i) => ({ ...r, rank: i + 1 }))
  }, [data, selectedTrack, selectedCar, selectedExperiment])

  // --- Loading skeleton ---
  if (loading) {
    return (
      <div className="space-y-4">
        <div className="flex flex-wrap gap-4">
          <Skeleton className="h-16 w-[180px]" />
          <Skeleton className="h-16 w-[180px]" />
          <Skeleton className="h-16 w-[180px]" />
        </div>
        <Skeleton className="h-64 w-full" />
      </div>
    )
  }

  // --- Error state ---
  if (error) {
    // 501 from the backend = measurements integration not configured.
    // S7 in the spec — route the user to Settings.
    if (error instanceof ApiError && error.status === 501) {
      return (
        <EmptyState
          icon={<Trophy className="h-12 w-12" />}
          title="Measurements service not configured"
          description="The leaderboard needs a measurements deployment configured. Set one up in Settings to enable best-lap queries."
          action={{
            label: "Open Settings",
            onClick: () => router.push("/settings"),
          }}
        />
      )
    }
    return (
      <EmptyState
        icon={<Trophy className="h-12 w-12" />}
        title="Failed to load leaderboard"
        description={error.message}
        action={{ label: "Retry", onClick: refetch }}
      />
    )
  }

  // --- Default render: dropdowns always visible, table shows empty state ---
  // Rendering the dropdowns even with zero data lets the user confirm the
  // page is alive while waiting for the first lap. The table handles its
  // own empty message.
  const isLakeEmpty = data.length === 0

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-4">
        <FilterSelect
          label="Track"
          value={selectedTrack}
          options={tracks}
          onChange={setSelectedTrack}
        />
        <FilterSelect
          label="Car"
          value={selectedCar}
          options={cars}
          onChange={setSelectedCar}
        />
        <FilterSelect
          label="Experiment"
          value={selectedExperiment}
          options={experiments}
          onChange={setSelectedExperiment}
        />
      </div>

      {isLakeEmpty ? (
        <EmptyState
          icon={<Trophy className="h-12 w-12" />}
          title="No laps recorded yet"
          description="Drive a session in Assetto Corsa with the telemetry source running. Laps appear here automatically once the first complete lap is recorded."
        />
      ) : (
        <LeaderboardTable data={rankedRows} />
      )}
    </div>
  )
}

interface FilterSelectProps {
  label: string
  value: string | null
  options: string[]
  onChange: (value: string) => void
}

function FilterSelect({ label, value, options, onChange }: FilterSelectProps) {
  return (
    <div className="flex flex-col gap-1.5">
      <label className="text-sm font-medium text-muted-foreground">
        {label}
      </label>
      <Select value={value ?? undefined} onValueChange={onChange}>
        <SelectTrigger className="w-[180px]">
          <SelectValue placeholder={`Select ${label.toLowerCase()}`} />
        </SelectTrigger>
        <SelectContent>
          {options.map((opt) => (
            <SelectItem key={opt} value={opt}>
              {opt}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  )
}
