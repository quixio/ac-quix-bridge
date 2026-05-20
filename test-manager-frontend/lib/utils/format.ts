/**
 * Formatting helpers shared across Analysis components.
 */

/**
 * Format a lap time expressed in milliseconds as `m:ss.mmm`.
 *
 * Examples:
 *   formatLapTime(98342)  === "1:38.342"
 *   formatLapTime(60001)  === "1:00.001"
 *   formatLapTime(59999)  === "0:59.999"
 *   formatLapTime(600000) === "10:00.000"
 *
 * Non-finite / negative values produce `"—"` so callers can render straight
 * into a table cell without a branch.
 */
export function formatLapTime(ms: number | null | undefined): string {
  if (ms == null || !Number.isFinite(ms) || ms < 0) return "—"

  const totalSeconds = Math.floor(ms / 1000)
  const millis = Math.floor(ms % 1000)
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60

  const ss = seconds.toString().padStart(2, "0")
  const mmm = millis.toString().padStart(3, "0")
  return `${minutes}:${ss}.${mmm}`
}
