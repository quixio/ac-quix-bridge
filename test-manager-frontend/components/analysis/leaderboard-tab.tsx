"use client"

import { useEffect, useMemo, useState } from "react"
import { ChevronsDown, ChevronsUp, Loader2 } from "lucide-react"

import { EmptyState } from "@/components/shared/empty-state"
import { Button } from "@/components/ui/button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { LivePositionsTable } from "@/components/analysis/live-positions-table"
import { BestLapsTable } from "@/components/analysis/best-laps-table"
import { useLiveStream } from "@/lib/hooks/use-live-stream"
import { COLLAPSED_ROW_COUNT } from "@/lib/utils/leaderboard-window"

/**
 * Multi-driver live-positions leaderboard.
 *
 * The page subscribes to `/api/v1/leaderboard/live-stream` and receives
 * one initial snapshot followed by per-tick active-row mutations and
 * occasional rebroadcast snapshots (triggered server-side when
 * historicals change). There is no HTTP polling — the WebSocket is the
 * sole data source.
 *
 * `useLiveStream` returns the same shape the old `useLivePositions`
 * polling hook exposed: `{ rows, tracks, cars, experiments, loading,
 * error }`, so the downstream filter / collapse logic is unchanged.
 *
 * On first load the dropdowns auto-select the alphabetically-first
 * value of each list. The selection sticks across snapshots; if the
 * currently-selected option disappears from the next snapshot
 * (shouldn't happen in sim mode but could in real mode) we re-snap
 * to the first available one.
 */
export function LeaderboardTab() {
  const { rows, tracks, cars, experiments, loading, error } = useLiveStream()

  const [track, setTrack] = useState<string | null>(null)
  const [car, setCar] = useState<string | null>(null)
  const [experiment, setExperiment] = useState<string | null>(null)
  // Default to collapsed — at 100+ historicals the unfiltered table is
  // unreadable. Button below the filter bar lifts the cap.
  const [collapsed, setCollapsed] = useState<boolean>(true)

  // Initial / fallback selection. We use the alphabetically-first option
  // — same pattern as the previous (pre-reset) leaderboard. The fallback
  // also catches the case where a previously-selected value isn't
  // present in the latest response.
  useEffect(() => {
    if (tracks.length === 0) return
    if (!track || !tracks.includes(track)) setTrack(tracks[0])
  }, [tracks, track])

  useEffect(() => {
    if (cars.length === 0) return
    if (!car || !cars.includes(car)) setCar(cars[0])
  }, [cars, car])

  useEffect(() => {
    if (experiments.length === 0) return
    if (!experiment || !experiments.includes(experiment)) {
      setExperiment(experiments[0])
    }
  }, [experiments, experiment])

  const filteredRows = useMemo(() => {
    if (!track || !car || !experiment) return []
    return rows.filter(
      (r) => r.track === track && r.car === car && r.experiment === experiment,
    )
  }, [rows, track, car, experiment])

  // Drives the two-vs-one-table layout below. Real mode has no active
  // driver when nobody is currently driving; LOCAL_DEV_MODE always has
  // the sim's "Ludvík" row active.
  const hasActive = useMemo(
    () => filteredRows.some((r) => r.is_active),
    [filteredRows],
  )

  if (loading && rows.length === 0) {
    return (
      <EmptyState
        icon={<Loader2 className="h-12 w-12 animate-spin" />}
        title="Loading leaderboard…"
        description="Fetching live positions from the backend."
      />
    )
  }

  if (error && rows.length === 0) {
    return (
      <EmptyState
        icon={<Loader2 className="h-12 w-12" />}
        title="Could not load leaderboard"
        description={error.message}
      />
    )
  }

  const canCollapse = filteredRows.length > COLLAPSED_ROW_COUNT

  return (
    <div className="flex w-full flex-col gap-6 py-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <FilterBar
          track={track}
          car={car}
          experiment={experiment}
          tracks={tracks}
          cars={cars}
          experiments={experiments}
          onTrack={setTrack}
          onCar={setCar}
          onExperiment={setExperiment}
        />
        {canCollapse && (
          <Button
            data-testid="leaderboard-collapse-toggle"
            variant="outline"
            size="sm"
            onClick={() => setCollapsed((v) => !v)}
          >
            {collapsed ? (
              <>
                <ChevronsDown className="mr-2 h-4 w-4" />
                Show all {filteredRows.length}
              </>
            ) : (
              <>
                <ChevronsUp className="mr-2 h-4 w-4" />
                Collapse to {COLLAPSED_ROW_COUNT}
              </>
            )}
          </Button>
        )}
      </div>
      {filteredRows.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No rows for this combination yet.
        </p>
      ) : hasActive ? (
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-[3fr_2fr] lg:gap-8">
          <LivePositionsTable rows={filteredRows} collapsed={collapsed} />
          <BestLapsTable rows={filteredRows} collapsed={collapsed} />
        </div>
      ) : (
        <div className="mx-auto w-full max-w-3xl">
          <p className="mb-3 text-sm text-muted-foreground">
            No live session right now — showing historical best laps
          </p>
          <BestLapsTable rows={filteredRows} collapsed={collapsed} />
        </div>
      )}
    </div>
  )
}

interface FilterBarProps {
  track: string | null
  car: string | null
  experiment: string | null
  tracks: string[]
  cars: string[]
  experiments: string[]
  onTrack: (v: string) => void
  onCar: (v: string) => void
  onExperiment: (v: string) => void
}

function FilterBar(props: FilterBarProps) {
  return (
    <div className="flex flex-wrap items-end gap-4">
      <FilterSelect
        label="Track"
        value={props.track}
        options={props.tracks}
        onChange={props.onTrack}
        testid="filter-track"
      />
      <FilterSelect
        label="Car"
        value={props.car}
        options={props.cars}
        onChange={props.onCar}
        testid="filter-car"
      />
      <FilterSelect
        label="Experiment"
        value={props.experiment}
        options={props.experiments}
        onChange={props.onExperiment}
        testid="filter-experiment"
      />
    </div>
  )
}

function FilterSelect({
  label,
  value,
  options,
  onChange,
  testid,
}: {
  label: string
  value: string | null
  options: string[]
  onChange: (v: string) => void
  testid: string
}) {
  return (
    <div className="flex min-w-[180px] flex-col gap-1">
      <label className="text-xs uppercase tracking-wider text-muted-foreground">
        {label}
      </label>
      <Select value={value ?? ""} onValueChange={onChange}>
        <SelectTrigger data-testid={testid}>
          <SelectValue placeholder={`Select ${label.toLowerCase()}`} />
        </SelectTrigger>
        <SelectContent>
          {options.map((o) => (
            <SelectItem key={o} value={o}>
              {o}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  )
}
