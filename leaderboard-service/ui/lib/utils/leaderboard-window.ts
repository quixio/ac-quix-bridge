/**
 * Collapse a sorted leaderboard array to a fixed-size "rank #1 + N around
 * a centre index" view. Used by both the Best Laps and Live Sector
 * tables so the default view stays readable when the historical field is
 * 100+ drivers wide.
 *
 * `collapseAroundActive` picks the centre by finding the active row.
 * `collapseAroundIndex` lets the caller pass an explicit centre index —
 * used together with `useAnchoredActiveIdx` to keep the visible window
 * stable for a couple of seconds while the active driver climbs through
 * it, instead of re-centring on every poll.
 */

import { useEffect, useState } from "react"

export const NEIGHBOR_WINDOW = 3
export const COLLAPSED_ROW_COUNT = 8

export function collapseAroundIndex<T>(
  items: T[],
  size: number,
  centerIdx: number,
): T[] {
  if (items.length <= size) return items
  if (centerIdx < 0 || centerIdx < size) return items.slice(0, size)

  const keep = new Set<number>()
  keep.add(0)
  for (
    let i = centerIdx - NEIGHBOR_WINDOW;
    i <= centerIdx + NEIGHBOR_WINDOW;
    i++
  ) {
    if (i >= 0 && i < items.length) keep.add(i)
  }

  let cursor = 1
  while (keep.size < size && cursor < items.length) {
    keep.add(cursor)
    cursor++
  }

  return [...keep].sort((a, b) => a - b).map((idx) => items[idx])
}

export function collapseAroundActive<T>(
  items: T[],
  size: number,
  isActive: (item: T) => boolean,
): T[] {
  const activeIdx = items.findIndex(isActive)
  return collapseAroundIndex(items, size, activeIdx)
}

/**
 * Lag the window's centre so the active driver's rank change is visible
 * *inside* the existing window before it re-centres. Returns the anchor
 * index to feed into `collapseAroundIndex`.
 *
 * Behaviour:
 * - First mount: anchor = current.
 * - Active still inside the anchored window: schedule a re-centre in
 *   `refreshDelayMs` (default 2.5 s). Restarts on each subsequent change.
 * - Active jumps outside the anchored window (or has no valid index):
 *   re-centre immediately so the user doesn't lose track of him.
 */
export function useAnchoredActiveIdx(
  currentActiveIdx: number,
  refreshDelayMs: number = 3500,
): number {
  const [anchor, setAnchor] = useState<number>(currentActiveIdx)

  useEffect(() => {
    if (currentActiveIdx < 0) return
    if (currentActiveIdx === anchor) return

    const insideWindow =
      currentActiveIdx === 0 ||
      (currentActiveIdx >= anchor - NEIGHBOR_WINDOW &&
        currentActiveIdx <= anchor + NEIGHBOR_WINDOW)

    if (!insideWindow) {
      setAnchor(currentActiveIdx)
      return
    }

    const id = setTimeout(() => setAnchor(currentActiveIdx), refreshDelayMs)
    return () => clearTimeout(id)
  }, [currentActiveIdx, anchor, refreshDelayMs])

  return anchor
}
