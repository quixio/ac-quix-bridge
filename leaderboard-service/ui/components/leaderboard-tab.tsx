"use client"

import { useCallback, useEffect, useMemo, useState } from "react"
import { Loader2 } from "lucide-react"

import { Button } from "@/components/ui/button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { LivePositionsTable } from "@/components/live-positions-table"
import {
  BestLapsTable,
  type BestLapRow,
} from "@/components/best-laps-table"
import type { ExperimentTree } from "@/lib/api/leaderboard"
import { useLiveStream } from "@/lib/hooks/use-live-stream"
import { useLeaderboardApi } from "@/lib/hooks/use-api"
import { cn } from "@/lib/utils"

const FOLLOW_LIVE_STORAGE_KEY = "leaderboard.followLive"

export function LeaderboardTab() {
  const leaderboardApi = useLeaderboardApi()
  const {
    rows: liveRows,
    isLive,
    liveCombo,
    freezeEvent,
  } = useLiveStream()

  const [tree, setTree] = useState<ExperimentTree>({})
  const [treeLoading, setTreeLoading] = useState(true)
  const [treeError, setTreeError] = useState<string | null>(null)

  const [bestLapsLoading, setBestLapsLoading] = useState(false)
  const [bestLapsError, setBestLapsError] = useState<string | null>(null)

  const [experiment, setExperiment] = useState<string | null>(null)
  const [track, setTrack] = useState<string | null>(null)
  const [car, setCar] = useState<string | null>(null)

  const [followLive, setFollowLive] = useState<boolean>(true)
  useEffect(() => {
    if (typeof window === "undefined") return
    const stored = window.localStorage.getItem(FOLLOW_LIVE_STORAGE_KEY)
    if (stored === null) {
      window.localStorage.setItem(FOLLOW_LIVE_STORAGE_KEY, "true")
      setFollowLive(true)
      return
    }
    setFollowLive(stored === "true")
  }, [])
  const handleFollowLiveChange = useCallback((next: boolean) => {
    setFollowLive(next)
    if (typeof window !== "undefined") {
      window.localStorage.setItem(
        FOLLOW_LIVE_STORAGE_KEY,
        next ? "true" : "false",
      )
    }
  }, [])

  const dropdownsDisabled = isLive && followLive
  const effectiveExperiment = dropdownsDisabled
    ? (liveCombo?.experiment ?? null)
    : experiment
  const effectiveTrack = dropdownsDisabled
    ? (liveCombo?.track ?? null)
    : track
  const effectiveCar = dropdownsDisabled ? (liveCombo?.car ?? null) : car

  const [bestLaps, setBestLaps] = useState<BestLapRow[]>([])

  useEffect(() => {
    let cancelled = false
    setTreeLoading(true)
    setTreeError(null)
    leaderboardApi
      .getExperimentTree()
      .then((data) => {
        if (cancelled) return
        setTree(data)
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setTreeError(
          err instanceof Error ? err.message : "Failed to load experiments",
        )
        setTree({})
      })
      .finally(() => {
        if (cancelled) return
        setTreeLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [leaderboardApi])

  const experimentOptions = useMemo<string[]>(
    () => Object.keys(tree).sort(),
    [tree],
  )
  const trackOptions = useMemo<string[]>(() => {
    if (!experiment) return []
    const tracks = tree[experiment]
    if (!tracks) return []
    return Object.keys(tracks).sort()
  }, [experiment, tree])
  const carOptions = useMemo<string[]>(() => {
    if (!experiment || !track) return []
    const cars = tree[experiment]?.[track]
    if (!cars) return []
    return [...cars].sort()
  }, [experiment, track, tree])

  useEffect(() => {
    setTrack(null)
    setCar(null)
  }, [experiment])

  useEffect(() => {
    setCar(null)
  }, [track])

  useEffect(() => {
    if (!effectiveExperiment || !effectiveTrack || !effectiveCar) {
      setBestLaps([])
      setBestLapsError(null)
      return
    }
    let cancelled = false
    setBestLapsLoading(true)
    setBestLapsError(null)
    leaderboardApi
      .getBestLaps(effectiveExperiment, effectiveTrack, effectiveCar)
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
  }, [effectiveExperiment, effectiveTrack, effectiveCar, leaderboardApi])

  const handleExperiment = useCallback((value: string) => {
    setExperiment(value || null)
  }, [])
  const handleTrack = useCallback((value: string) => {
    setTrack(value || null)
  }, [])
  const handleCar = useCallback((value: string) => {
    setCar(value || null)
  }, [])

  const bothSelected = Boolean(
    effectiveExperiment && effectiveTrack && effectiveCar,
  )

  const liveTableRows = useMemo(() => liveRows, [liveRows])

  return (
    <div className="flex w-full flex-col gap-6 py-6">
      <FilterBar
        experiment={experiment}
        track={track}
        car={car}
        experiments={experimentOptions}
        tracks={trackOptions}
        cars={carOptions}
        treeLoading={treeLoading}
        treeError={treeError}
        onExperiment={handleExperiment}
        onTrack={handleTrack}
        onCar={handleCar}
        disabledOverride={dropdownsDisabled}
        isLive={isLive}
        followLive={followLive}
        onFollowLiveChange={handleFollowLiveChange}
        liveCombo={liveCombo}
      />
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[3fr_2fr] lg:gap-8">
        <LivePositionsTable
          rows={liveTableRows}
          isLive={isLive}
          freezeEvent={freezeEvent}
        />
        <BestLapsPanel
          experiment={effectiveExperiment}
          track={effectiveTrack}
          car={effectiveCar}
          bothSelected={bothSelected}
          loading={bestLapsLoading}
          error={bestLapsError}
          rows={bestLaps}
          isLive={isLive}
          followLive={followLive}
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
  treeLoading: boolean
  treeError: string | null
  onExperiment: (v: string) => void
  onTrack: (v: string) => void
  onCar: (v: string) => void
  disabledOverride: boolean
  isLive: boolean
  followLive: boolean
  onFollowLiveChange: (next: boolean) => void
  liveCombo: { experiment: string; track: string; car: string } | null
}

function FilterBar(props: FilterBarProps) {
  const experimentDisabled =
    props.disabledOverride ||
    props.treeLoading ||
    props.experiments.length === 0
  const trackDisabled =
    props.disabledOverride ||
    props.treeLoading ||
    !props.experiment ||
    props.tracks.length === 0
  const carDisabled =
    props.disabledOverride ||
    props.treeLoading ||
    !props.experiment ||
    !props.track ||
    props.cars.length === 0

  const showExperiment = props.disabledOverride
    ? (props.liveCombo?.experiment ?? null)
    : props.experiment
  const showTrack = props.disabledOverride
    ? (props.liveCombo?.track ?? null)
    : props.track
  const showCar = props.disabledOverride
    ? (props.liveCombo?.car ?? null)
    : props.car

  const displayedExperiments = useMemo(() => {
    if (showExperiment && !props.experiments.includes(showExperiment)) {
      return [showExperiment, ...props.experiments]
    }
    return props.experiments
  }, [showExperiment, props.experiments])
  const displayedTracks = useMemo(() => {
    if (showTrack && !props.tracks.includes(showTrack)) {
      return [showTrack, ...props.tracks]
    }
    return props.tracks
  }, [showTrack, props.tracks])
  const displayedCars = useMemo(() => {
    if (showCar && !props.cars.includes(showCar)) {
      return [showCar, ...props.cars]
    }
    return props.cars
  }, [showCar, props.cars])

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-end gap-4">
        <FilterSelect
          label="Experiment"
          value={showExperiment}
          options={displayedExperiments}
          onChange={props.onExperiment}
          testid="filter-experiment"
          disabled={experimentDisabled}
          loading={props.treeLoading}
        />
        <FilterSelect
          label="Track"
          value={showTrack}
          options={displayedTracks}
          onChange={props.onTrack}
          testid="filter-track"
          disabled={trackDisabled || displayedTracks.length === 0}
          loading={props.treeLoading}
        />
        <FilterSelect
          label="Car"
          value={showCar}
          options={displayedCars}
          onChange={props.onCar}
          testid="filter-car"
          disabled={carDisabled || displayedCars.length === 0}
          loading={props.treeLoading}
        />
        {props.isLive && (
          <FollowLiveToggle
            value={props.followLive}
            onChange={props.onFollowLiveChange}
          />
        )}
      </div>
      {props.treeError && (
        <p className="text-sm text-rose-400">{props.treeError}</p>
      )}
    </div>
  )
}

function FollowLiveToggle({
  value,
  onChange,
}: {
  value: boolean
  onChange: (next: boolean) => void
}) {
  return (
    <div className="flex min-w-[180px] flex-col gap-1">
      <label className="text-xs uppercase tracking-wider text-muted-foreground">
        Follow live driver
      </label>
      <Button
        type="button"
        variant={value ? "default" : "outline"}
        size="sm"
        onClick={() => onChange(!value)}
        data-testid="follow-live-toggle"
        aria-pressed={value}
        className={cn(
          "justify-start",
          value && "bg-blue-500 text-white hover:bg-blue-500/90",
        )}
      >
        <span
          className={cn(
            "mr-2 inline-block h-2 w-2 rounded-full",
            value ? "bg-white" : "bg-muted-foreground",
          )}
        />
        {value ? "ON" : "OFF"}
      </Button>
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
  isLive: boolean
  followLive: boolean
}

function BestLapsPanel(props: BestLapsPanelProps) {
  if (!props.experiment || !props.track || !props.car) {
    return (
      <BestLapsPlaceholder message="Pick experiment / track / car to view best laps." />
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
    const message =
      props.isLive && props.followLive
        ? "No historical laps yet for this experiment / track / car."
        : "No best laps recorded for this combination yet."
    return <BestLapsPlaceholder message={message} />
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
