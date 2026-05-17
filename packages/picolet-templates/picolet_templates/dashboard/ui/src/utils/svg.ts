/**
 * svg.ts — pure SVG path computation utilities for hand-rolled charts.
 *
 * No imports. No side effects. All functions take plain arrays of numbers and
 * return SVG path `d` attribute strings.
 *
 * Used by CpuChart, NetworkChart, DiskChart (line+area), MemoryGauge (arc),
 * and SparklineStrip (sparkline per core).
 *
 * F9, F10, F11, F12 in PH22 plan.
 */

/**
 * Build a polyline path from a series of values.
 *
 * @param values  Array of numeric samples (≥ 2 needed for a visible line).
 * @param max     Y-axis maximum. Values are clamped to [0, max].
 * @param w       SVG viewport width in px.
 * @param h       SVG viewport height in px.
 * @returns       SVG `d` string starting with M, or '' if fewer than 2 samples.
 */
export function toLinePath(values: number[], max: number, w: number, h: number): string {
  if (values.length < 2) return ''
  const n = values.length
  const step = w / (n - 1)
  const pts = values.map((v, i) => {
    const x = i * step
    const clamped = Math.max(0, Math.min(max, v))
    const y = h - (max > 0 ? (clamped / max) * h : 0)
    return `${x.toFixed(1)},${y.toFixed(1)}`
  })
  return 'M ' + pts.join(' L ')
}

/**
 * Build a closed area path (line + fill to baseline).
 *
 * @param values  Array of numeric samples (≥ 2 needed).
 * @param max     Y-axis maximum.
 * @param w       SVG viewport width in px.
 * @param h       SVG viewport height in px.
 * @returns       SVG `d` string for a closed polygon, or '' if fewer than 2 samples.
 */
export function toAreaPath(values: number[], max: number, w: number, h: number): string {
  if (values.length < 2) return ''
  const line = toLinePath(values, max, w, h)
  const n = values.length
  const step = w / (n - 1)
  const lastX = ((n - 1) * step).toFixed(1)
  return `${line} L ${lastX},${h} L 0,${h} Z`
}

/**
 * Build an SVG arc path for a radial gauge.
 *
 * Arc sweeps from 225° to 315° (270° total span, bottom-gap style).
 * pct=0 → no arc. pct=100 → full 270° sweep.
 *
 * @param pct   Fill percentage 0–100.
 * @param cx    Centre X of the arc.
 * @param cy    Centre Y of the arc.
 * @param r     Arc radius.
 * @returns     SVG `d` string for the arc, or '' if pct ≤ 0.
 */
export function toGaugePath(pct: number, cx: number, cy: number, r: number): string {
  if (pct <= 0) return ''
  const clamped = Math.max(0, Math.min(100, pct))
  const startAngle = 225 * Math.PI / 180
  const sweepAngle = 270 * Math.PI / 180 * (clamped / 100)
  const endAngle = startAngle + sweepAngle
  const x1 = cx + r * Math.cos(startAngle)
  const y1 = cy + r * Math.sin(startAngle)
  const x2 = cx + r * Math.cos(endAngle)
  const y2 = cy + r * Math.sin(endAngle)
  const large = sweepAngle > Math.PI ? 1 : 0
  return `M ${x1.toFixed(2)} ${y1.toFixed(2)} A ${r} ${r} 0 ${large} 1 ${x2.toFixed(2)} ${y2.toFixed(2)}`
}

/**
 * Background arc for the gauge — full 270° sweep at low opacity.
 */
export function toGaugeBgPath(cx: number, cy: number, r: number): string {
  return toGaugePath(100, cx, cy, r)
}
