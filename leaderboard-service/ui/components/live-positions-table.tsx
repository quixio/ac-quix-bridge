"use client"

import { useEffect, useMemo, useRef, useState } from "react"
import { useAutoAnimate } from "@formkit/auto-animate/react"

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { formatLapTime } from "@/lib/utils/format"
import { cn } from "@/lib/utils"
import {
  COLLAPSED_ROW_COUNT,
  collapseAroundIndex,
  useAnchoredActiveIdx,
} from "@/lib/utils/leaderboard-window"
import type { FreezeEvent } from "@/lib/hooks/use-live-stream"
import type { LivePositionEntry } from "@/types/leaderboard"

export interface LivePositionsTableProps {
  rows: LivePositionEntry[]
  freezeEvent?: FreezeEvent | null
  isLive?: boolean
}

const FREEZE_MS = Number(process.env.NEXT_PUBLIC_FREEZE_MS) || 200

type FreezeMode = "live" | "frozen"

interface FreezeState {
  mode: FreezeMode
  stamp: number
}

interface CrossingSnapshot {
  stamp: number
  activeAtMs: number
  activeAtGateMs: number
  historicalAtMs: Record<string, number>
}

export function LivePositionsTable({
  rows,
  freezeEvent = null,
  isLive = true,
}: LivePositionsTableProps) {
  const sorted = useMemo(
    () => [...rows].sort((a, b) => a.rank - b.rank),
    [rows],
  )

  const currentActiveIdx = sorted.findIndex((r) => r.is_active)
  const anchorIdx = useAnchoredActiveIdx(currentActiveIdx)

  const [expanded, setExpanded] = useState(false)
  const visible = expanded
    ? sorted
    : collapseAroundIndex(sorted, COLLAPSED_ROW_COUNT, anchorIdx)

  const [bodyRef] = useAutoAnimate<HTMLTableSectionElement>({
    duration: 700,
    easing: "ease-in-out",
  })

  const [freezeState, setFreezeState] = useState<FreezeState>({
    mode: "live",
    stamp: 0,
  })
  const [crossingSnapshot, setCrossingSnapshot] =
    useState<CrossingSnapshot | null>(null)
  const latestFreezeStampRef = useRef<number>(0)
  const freezeEventRef = useRef<FreezeEvent | null>(null)
  freezeEventRef.current = freezeEvent

  useEffect(() => {
    const ev = freezeEventRef.current
    if (!ev) return
    latestFreezeStampRef.current = ev.stamp
    const stampAtSchedule = ev.stamp
    setCrossingSnapshot({
      stamp: ev.stamp,
      activeAtMs: ev.activeAtCrossingMs,
      activeAtGateMs: ev.activeAtCrossingGateMs ?? ev.activeAtCrossingMs,
      historicalAtMs: ev.historicalAtCrossing,
    })
    setFreezeState({ mode: "frozen", stamp: ev.stamp })
    const t = setTimeout(() => {
      if (latestFreezeStampRef.current === stampAtSchedule) {
        setFreezeState({ mode: "live", stamp: stampAtSchedule })
      }
    }, FREEZE_MS)
    return () => clearTimeout(t)
  }, [freezeEvent?.stamp])

  const activeRow = sorted.find((r) => r.is_active) ?? null
  const activeLastGateIdx = activeRow?.last_gate_index ?? null
  useEffect(() => {
    if (activeLastGateIdx === null || activeLastGateIdx === undefined) {
      setFreezeState((prev) =>
        prev.mode === "live" ? prev : { mode: "live", stamp: prev.stamp },
      )
    }
  }, [activeLastGateIdx])

  if (!isLive) {
    return (
      <div className="w-full">
        <div className="mb-2">
          <h3 className="text-base font-semibold">Live Sector Comparison</h3>
          <p className="text-xs text-muted-foreground">
            Re-ranks at checkpoint gates
          </p>
        </div>
        <div className="flex items-center gap-2 rounded border border-dashed border-muted-foreground/30 p-4 text-sm text-muted-foreground">
          <span>
            No live session — start an AC session to see live sector deltas.
          </span>
        </div>
      </div>
    )
  }

  return (
    <div className="w-full">
      <div className="mb-2 flex items-end justify-between gap-2">
        <div>
          <h3 className="text-base font-semibold">Live Sector Comparison</h3>
          <p className="text-xs text-muted-foreground">
            Re-ranks at checkpoint gates
          </p>
        </div>
        {sorted.length > COLLAPSED_ROW_COUNT && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => setExpanded((v) => !v)}
            data-testid="see-all-toggle"
          >
            {expanded ? "Show top 8" : "See all"}
          </Button>
        )}
      </div>
      <Table className="table-fixed">
        <TableHeader>
          <TableRow>
            <TableHead className="w-[64px]">Rank</TableHead>
            <TableHead className="w-[240px]">Driver</TableHead>
            <TableHead className="w-[160px] text-right tabular-nums">
              Best Lap
            </TableHead>
            <TableHead className="text-right tabular-nums">
              At Position
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody ref={bodyRef}>
          {visible.map((row, idx) => {
            const aboveDriver = idx > 0 ? visible[idx - 1].driver : null
            const belowDriver =
              idx < visible.length - 1 ? visible[idx + 1].driver : null
            const aboveRow = idx > 0 ? visible[idx - 1] : null
            const belowRow =
              idx < visible.length - 1 ? visible[idx + 1] : null
            const aboveAtCrossingMs =
              aboveDriver != null && crossingSnapshot
                ? crossingSnapshot.historicalAtMs[aboveDriver] ??
                  aboveRow?.current_lap_time_ms ??
                  null
                : aboveRow?.current_lap_time_ms ?? null
            const belowAtCrossingMs =
              belowDriver != null && crossingSnapshot
                ? crossingSnapshot.historicalAtMs[belowDriver] ??
                  belowRow?.current_lap_time_ms ??
                  null
                : belowRow?.current_lap_time_ms ?? null
            return (
              <LeaderRow
                key={`${row.driver}|${row.track}|${row.car}|${row.experiment}|${row.is_active ? "live" : "ghost"}`}
                row={row}
                freezeState={freezeState}
                crossingSnapshot={crossingSnapshot}
                aboveAtCrossingMs={aboveAtCrossingMs}
                belowAtCrossingMs={belowAtCrossingMs}
              />
            )
          })}
        </TableBody>
      </Table>
    </div>
  )
}

function rowDisplayMs(
  row: LivePositionEntry,
  freezeState: FreezeState,
  crossingSnapshot: CrossingSnapshot | null,
): number {
  if (freezeState.mode === "frozen" && crossingSnapshot != null) {
    if (row.is_active) {
      return crossingSnapshot.activeAtMs
    }
    const fromMap = crossingSnapshot.historicalAtMs[row.driver]
    if (typeof fromMap === "number") {
      return fromMap
    }
    return row.current_lap_time_ms
  }
  return row.current_lap_time_ms
}

function formatGapMs(deltaMs: number, sign: "+" | "-"): string {
  const abs = Math.abs(deltaMs) / 1000
  return `${sign}${abs.toFixed(3)}`
}

function LeaderRow({
  row,
  freezeState,
  crossingSnapshot,
  aboveAtCrossingMs,
  belowAtCrossingMs,
}: {
  row: LivePositionEntry
  freezeState: FreezeState
  crossingSnapshot: CrossingSnapshot | null
  aboveAtCrossingMs: number | null
  belowAtCrossingMs: number | null
}) {
  const isFrozen = freezeState.mode === "frozen"
  const displayMs = rowDisplayMs(row, freezeState, crossingSnapshot)
  const atPosLabel = formatLapTime(displayMs)

  let atPosClass = ""
  if (row.is_active && isFrozen) {
    atPosClass = "font-semibold text-blue-400"
  }

  const activeRef =
    crossingSnapshot?.activeAtGateMs ??
    crossingSnapshot?.activeAtMs ??
    (row.is_active ? row.current_lap_time_ms : null)
  // Only show gap chips once the active driver has actually crossed a gate
  // on the CURRENT lap (last_gate_index != null). Right after a lap rollover
  // — and in the transient window where completedLaps increments before
  // iCurrentTime resets — the gate index is null while the lap clock is
  // still high; computing a gap then compares the active's stale high time
  // against the historicals' gate-0 fallback and renders a garbage value
  // (e.g. +112s). No crossed gate ⇒ no gap.
  const hasGate = row.last_gate_index != null
  const gapAbove =
    row.is_active && hasGate && activeRef != null && aboveAtCrossingMs != null
      ? Math.max(0, activeRef - aboveAtCrossingMs)
      : null
  const gapBelow =
    row.is_active && hasGate && activeRef != null && belowAtCrossingMs != null
      ? Math.max(0, belowAtCrossingMs - activeRef)
      : null

  return (
    <TableRow
      data-testid={`leader-row-${row.driver}`}
      data-active={row.is_active ? "true" : "false"}
      className={cn(
        row.is_active &&
          "border-l-4 border-l-blue-500 bg-blue-500/10 font-medium",
      )}
    >
      <TableCell className="tabular-nums">{row.rank}</TableCell>
      <TableCell>
        <div className="flex items-center gap-2">
          <span>{row.driver}</span>
          {row.is_active && (
            <>
              <Badge
                data-testid="live-badge"
                variant="default"
                className="bg-blue-500 text-white hover:bg-blue-500"
              >
                LIVE
              </Badge>
              {row.current_lap != null && (
                <span className="inline-block w-[56px] text-xs uppercase tracking-wider tabular-nums text-muted-foreground">
                  Lap {row.current_lap}
                </span>
              )}
            </>
          )}
        </div>
      </TableCell>
      <TableCell className="text-right tabular-nums">
        <BestLapCell ms={row.best_lap_ms} lapNumber={row.best_lap_number} />
      </TableCell>
      <TableCell className="text-right tabular-nums">
        <div className="flex items-center justify-end gap-2">
          <span className={cn(atPosClass)}>{atPosLabel}</span>
          {row.is_active && gapAbove != null && gapAbove > 0 && (
            <span className="text-xs font-semibold tabular-nums text-rose-400">
              {formatGapMs(gapAbove, "+")}
            </span>
          )}
          {row.is_active && gapBelow != null && gapBelow > 0 && (
            <span className="text-xs font-semibold tabular-nums text-emerald-400">
              {formatGapMs(gapBelow, "-")}
            </span>
          )}
        </div>
      </TableCell>
    </TableRow>
  )
}

export function BestLapCell({
  ms,
  lapNumber,
}: {
  ms: number | null
  lapNumber: number | null
}) {
  if (ms == null) return <>—</>
  return (
    <>
      <span>{formatLapTime(ms)}</span>
      {lapNumber != null && (
        <span className="ml-1 text-muted-foreground">(L{lapNumber})</span>
      )}
    </>
  )
}
