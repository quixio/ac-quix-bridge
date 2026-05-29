"use client"

import { useCallback, useEffect, useMemo, useState } from "react"
import { Loader2 } from "lucide-react"

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { LivePositionsTable } from "@/components/analysis/live-positions-table"
import {
  BestLapsTable,
  type BestLapRow,
} from "@/components/analysis/best-laps-table"
import { useLiveStream } from "@/lib/hooks/use-live-stream"
import { useLeaderboardApi } from "@/lib/hooks/use-api"

/**
 * Multi-driver leaderboard — Step 1.5.
 *
 * Two independent panels:
 *
 *   * Left: Live Sector Comparison. Still driven by the WebSocket via
 *     `useLiveStream`. We do not filter its rows by the dropdown values
 *     in this step — it renders whatever the WS provides (typically
 *     empty, or a single live driver). Step 2 will gate this on the
 *     dropdown selection again.
 *
 *   * Right: Best Laps. Driven by three new REST endpoints under
 *     `/api/v1/leaderboard/`:
 *       - `GET /experiments`
 *       - `GET /experiment-options?experiment=...`
 *       - `GET /best-laps?experiment=...&track=...&car=...`
 *
 * Dropdown UX:
 *   1. Mount → fetch experiments → Experiment dropdown populated, Track
 *      and Car dropdowns disabled.
 *   2. Pick Experiment → fetch (tracks, cars) → both dropdowns enabled
 *      and reset to "unselected". Best Laps cleared.
 *   3. Pick both Track AND Car → fetch best-laps → table populates.
 *   4. Change Experiment → Track + Car selections clear; Best Laps
 *      cleared; dropdowns re-populate.
 *
 * No auto-select-first behaviour — the user must explicitly choose
 * every dropdown.
 */
export function LeaderboardTab() {
  const leaderboardApi = useLeaderboardApi()
  const { rows: liveRows } = useLiveStream()

  // Dropdown options.
  const [experiments, setExperiments] = useState<string[]>([])
  const [tracks, setTracks] = useState<string[]>([])
  const [cars, setCars] = useState<string[]>([])

  // Loading / error state per fetch surface.
  const [experimentsLoading, setExperimentsLoading] = useState(true)
  const [experimentsError, setExperimentsError] = useState<string | null>(null)
  const [optionsLoading, setOptionsLoading] = useState(false)
  const [optionsError, setOptionsError] = useState<string | null>(null)
  const [bestLapsLoading, setBestLapsLoading] = useState(false)
  const [bestLapsError, setBestLapsError] = useState<string | null>(null)

  // Selections.
  const [experiment, setExperiment] = useState<string | null>(null)
  const [track, setTrack] = useState<string | null>(null)
  const [car, setCar] = useState<string | null>(null)

  // Best Laps payload.
  const [bestLaps, setBestLaps] = useState<BestLapRow[]>([])

  // 1. Fetch the experiment list on mount.
  useEffect(() => {
    let cancelled = false
    setExperimentsLoading(true)
    setExperimentsError(null)
    leaderboardApi
      .getExperiments()
      .then((data) => {
        if (cancelled) return
        setExperiments(data)
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setExperimentsError(
          err instanceof Error ? err.message : "Failed to load experiments",
        )
      })
      .finally(() => {
        if (cancelled) return
        setExperimentsLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [leaderboardApi])

  // 2. When experiment changes, refetch (tracks, cars). Reset downstream
  // selections + Best Laps. When experiment is cleared, blank everything.
  useEffect(() => {
    setTrack(null)
    setCar(null)
    setBestLaps([])
    setBestLapsError(null)
    if (!experiment) {
      setTracks([])
      setCars([])
      setOptionsError(null)
      return
    }
    let cancelled = false
    setOptionsLoading(true)
    setOptionsError(null)
    leaderboardApi
      .getExperimentOptions(experiment)
      .then((data) => {
        if (cancelled) return
        setTracks(data.tracks)
        setCars(data.cars)
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setOptionsError(
          err instanceof Error ? err.message : "Failed to load options",
        )
        setTracks([])
        setCars([])
      })
      .finally(() => {
        if (cancelled) return
        setOptionsLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [experiment, leaderboardApi])

  // 3. When Track AND Car are both selected, fetch best laps.
  useEffect(() => {
    if (!experiment || !track || !car) {
      setBestLaps([])
      setBestLapsError(null)
      return
    }
    let cancelled = false
    setBestLapsLoading(true)
    setBestLapsError(null)
    leaderboardApi
      .getBestLaps(experiment, track, car)
      .then((data) => {
        if (cancelled) return
        setBestLaps(data)
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setBestLapsError(
          err instanceof Error ? err.message : "Failed to load best laps",
        )
        setBestLaps([])
      })
      .finally(() => {
        if (cancelled) return
        setBestLapsLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [experiment, track, car, leaderboardApi])

  // Stable callbacks for the Select components.
  const handleExperiment = useCallback((value: string) => {
    setExperiment(value || null)
  }, [])
  const handleTrack = useCallback((value: string) => {
    setTrack(value || null)
  }, [])
  const handleCar = useCallback((value: string) => {
    setCar(value || null)
  }, [])

  const bothSelected = Boolean(experiment && track && car)

  // Live Sector Comparison still consumes the WS rows directly. In Step
  // 2 we'll filter by the dropdown selection again, but for now we
  // honour the spec: "leave unchanged".
  const liveTableRows = useMemo(() => liveRows, [liveRows])

  return (
    <div className="flex w-full flex-col gap-6 py-6">
      <FilterBar
        experiment={experiment}
        track={track}
        car={car}
        experiments={experiments}
        tracks={tracks}
        cars={cars}
        experimentsLoading={experimentsLoading}
        optionsLoading={optionsLoading}
        experimentsError={experimentsError}
        optionsError={optionsError}
        onExperiment={handleExperiment}
        onTrack={handleTrack}
        onCar={handleCar}
      />
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[3fr_2fr] lg:gap-8">
        <LivePositionsTable rows={liveTableRows} collapsed={false} />
        <BestLapsPanel
          experiment={experiment}
          track={track}
          car={car}
          bothSelected={bothSelected}
          loading={bestLapsLoading}
          error={bestLapsError}
          rows={bestLaps}
        />
      </div>
    </div>
  )
}

interface FilterBarProps {
  experiment: string | null
  track: string | null
  car: string | null
  experiments: string[]
  tracks: string[]
  cars: string[]
  experimentsLoading: boolean
  optionsLoading: boolean
  experimentsError: string | null
  optionsError: string | null
  onExperiment: (v: string) => void
  onTrack: (v: string) => void
  onCar: (v: string) => void
}

function FilterBar(props: FilterBarProps) {
  const experimentDisabled = props.experimentsLoading || props.experiments.length === 0
  const downstreamDisabled =
    !props.experiment || props.optionsLoading || Boolean(props.optionsError)

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-end gap-4">
        <FilterSelect
          label="Experiment"
          value={props.experiment}
          options={props.experiments}
          onChange={props.onExperiment}
          testid="filter-experiment"
          disabled={experimentDisabled}
          loading={props.experimentsLoading}
        />
        <FilterSelect
          label="Track"
          value={props.track}
          options={props.tracks}
          onChange={props.onTrack}
          testid="filter-track"
          disabled={downstreamDisabled || props.tracks.length === 0}
          loading={props.optionsLoading}
        />
        <FilterSelect
          label="Car"
          value={props.car}
          options={props.cars}
          onChange={props.onCar}
          testid="filter-car"
          disabled={downstreamDisabled || props.cars.length === 0}
          loading={props.optionsLoading}
        />
      </div>
      {props.experimentsError && (
        <p className="text-sm text-rose-400">{props.experimentsError}</p>
      )}
      {props.optionsError && (
        <p className="text-sm text-rose-400">{props.optionsError}</p>
      )}
    </div>
  )
}

function FilterSelect({
  label,
  value,
  options,
  onChange,
  testid,
  disabled,
  loading,
}: {
  label: string
  value: string | null
  options: string[]
  onChange: (v: string) => void
  testid: string
  disabled?: boolean
  loading?: boolean
}) {
  return (
    <div className="flex min-w-[180px] flex-col gap-1">
      <label className="flex items-center gap-2 text-xs uppercase tracking-wider text-muted-foreground">
        <span>{label}</span>
        {loading && <Loader2 className="h-3 w-3 animate-spin" />}
      </label>
      <Select
        value={value ?? ""}
        onValueChange={onChange}
        disabled={disabled}
      >
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

interface BestLapsPanelProps {
  experiment: string | null
  track: string | null
  car: string | null
  bothSelected: boolean
  loading: boolean
  error: string | null
  rows: BestLapRow[]
}

function BestLapsPanel(props: BestLapsPanelProps) {
  if (!props.experiment) {
    return (
      <BestLapsPlaceholder message="Select an experiment to begin." />
    )
  }
  if (!props.bothSelected) {
    return (
      <BestLapsPlaceholder message="Select a track and car to view best laps." />
    )
  }
  if (props.loading) {
    return (
      <BestLapsPlaceholder
        message="Loading best laps…"
        icon={<Loader2 className="h-5 w-5 animate-spin" />}
      />
    )
  }
  if (props.error) {
    return (
      <BestLapsPlaceholder message={`Could not load best laps: ${props.error}`} />
    )
  }
  if (props.rows.length === 0) {
    return (
      <BestLapsPlaceholder message="No best laps recorded for this combination yet." />
    )
  }
  return <BestLapsTable rows={props.rows} />
}

function BestLapsPlaceholder({
  message,
  icon,
}: {
  message: string
  icon?: React.ReactNode
}) {
  return (
    <div className="w-full">
      <div className="mb-2">
        <h3 className="text-base font-semibold">Best Laps</h3>
      </div>
      <div className="flex items-center gap-2 rounded border border-dashed border-muted-foreground/30 p-4 text-sm text-muted-foreground">
        {icon}
        <span>{message}</span>
      </div>
    </div>
  )
}
