/**
 * format.ts — display formatting utilities for dashboard metric values.
 *
 * No imports. No side effects.
 */

/**
 * Format a percentage to one decimal place.
 * Returns '--' for null/undefined/NaN.
 */
export function fmtPct(v: number | null | undefined): string {
  if (v == null || isNaN(v)) return '--'
  return v.toFixed(1)
}

/**
 * Format bytes/sec to a human-readable string with appropriate unit.
 * Auto-selects B/s, KB/s, MB/s, GB/s.
 */
export function fmtBytes(bps: number | null | undefined): string {
  if (bps == null || isNaN(bps)) return '--'
  if (bps < 1024) return `${bps.toFixed(0)} B/s`
  if (bps < 1024 * 1024) return `${(bps / 1024).toFixed(1)} KB/s`
  if (bps < 1024 * 1024 * 1024) return `${(bps / (1024 * 1024)).toFixed(1)} MB/s`
  return `${(bps / (1024 * 1024 * 1024)).toFixed(2)} GB/s`
}

/**
 * Format an uptime in seconds to HH:MM:SS or D days HH:MM:SS.
 */
export function fmtUptime(s: number | null | undefined): string {
  if (s == null || isNaN(s) || s < 0) return '--:--:--'
  const total = Math.floor(s)
  const secs = total % 60
  const mins = Math.floor(total / 60) % 60
  const hours = Math.floor(total / 3600) % 24
  const days = Math.floor(total / 86400)
  const hh = String(hours).padStart(2, '0')
  const mm = String(mins).padStart(2, '0')
  const ss = String(secs).padStart(2, '0')
  if (days > 0) return `${days}d ${hh}:${mm}:${ss}`
  return `${hh}:${mm}:${ss}`
}

/**
 * Format a load-average value to two decimal places.
 */
export function fmtLoad(v: number | null | undefined): string {
  if (v == null || isNaN(v)) return '--'
  return v.toFixed(2)
}
