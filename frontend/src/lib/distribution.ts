// Pure math helpers for the commit-count distribution.
//
// Mirrors the sampling logic in `backend/shared/distribution.py`. The curve is
// a list of `[x_norm, y]` control points where x_norm ∈ [0, 1] spans the
// configured [min, max] commit range and y is a non-negative relative weight.
// The backend linearly interpolates the curve at each integer position and
// draws a single categorical sample; the dashboard shows the same analytical
// probabilities so what you sculpt is exactly what the executor will see.

/**
 * A control point is just `[x_norm, y]`. We use `number[]` rather than a
 * `[number, number]` tuple so the type lines up cleanly with the API surface
 * (JSON parsing produces `number[][]`, which TypeScript can't narrow further).
 * Every call site treats indices 0 and 1 only.
 */
export type CurvePoint = number[]
export type Curve = CurvePoint[]

export const clamp = (v: number, lo: number, hi: number): number => (v < lo ? lo : v > hi ? hi : v)

/** Round to 4 decimal places for stable JSON comparison and serialization. */
const r4 = (v: number): number => Math.round(v * 10_000) / 10_000

/**
 * Linear interpolation of the curve at a normalized x in [0, 1]. The curve is
 * sorted by x; outside the input range we extend with the nearest endpoint's
 * y value (clamped non-negative).
 */
export function interpolateCurve(curve: Curve, x: number): number {
  if (curve.length === 0) return 1
  const sorted = [...curve].sort((a, b) => a[0] - b[0])
  if (x <= sorted[0][0]) return Math.max(0, sorted[0][1])
  if (x >= sorted[sorted.length - 1][0]) return Math.max(0, sorted[sorted.length - 1][1])
  for (let i = 1; i < sorted.length; i++) {
    const [x0, y0] = sorted[i - 1]
    const [x1, y1] = sorted[i]
    if (x <= x1) {
      const span = x1 - x0
      if (span === 0) return Math.max(0, y0)
      const t = (x - x0) / span
      return Math.max(0, y0 + t * (y1 - y0))
    }
  }
  return Math.max(0, sorted[sorted.length - 1][1])
}

/**
 * Resample an arbitrary curve into `n` control points uniformly spaced across
 * x ∈ [0, 1]. Used to project the persisted curve onto the integer positions
 * the editor wants to manipulate.
 */
export function sampleCurveAtN(curve: Curve, n: number): Curve {
  if (n <= 0) return []
  if (n === 1) return [[0, Math.max(0, curve[0]?.[1] ?? 1)]]
  return Array.from({ length: n }, (_, i) => {
    const x = i / (n - 1)
    return [r4(x), r4(interpolateCurve(curve, x))]
  })
}

/**
 * The analytical probability of each integer count in [min, max] given the
 * curve. Falls back to uniform when the curve has zero total weight.
 */
export function computeProbabilities(
  min: number,
  max: number,
  curve: Curve,
): { count: number; prob: number }[] {
  if (max === min) return [{ count: min, prob: 1 }]
  const n = max - min + 1
  const points = sampleCurveAtN(curve, n)
  const weights = points.map(([, y]) => Math.max(0, y))
  const sum = weights.reduce((a, b) => a + b, 0)
  if (sum <= 0) return points.map((_, i) => ({ count: min + i, prob: 1 / n }))
  return weights.map((w, i) => ({ count: min + i, prob: w / sum }))
}

export function expectedValue(probs: { count: number; prob: number }[]): number {
  return probs.reduce((acc, { count, prob }) => acc + count * prob, 0)
}

/** True when two curves agree to within a small epsilon at every point. */
export function curvesEqual(a: Curve, b: Curve, eps = 1e-3): boolean {
  if (a.length !== b.length) return false
  for (let i = 0; i < a.length; i++) {
    if (Math.abs(a[i][0] - b[i][0]) > eps) return false
    if (Math.abs(a[i][1] - b[i][1]) > eps) return false
  }
  return true
}

// ---------------------------------------------------------------------------
// Presets — each takes the number of points n = max - min + 1 and returns
// uniformly-spaced control points spanning x ∈ [0, 1] with y ∈ [0, 1].
// ---------------------------------------------------------------------------

const xFor = (i: number, n: number): number => (n <= 1 ? 0 : i / (n - 1))

export function presetUniform(n: number): Curve {
  return Array.from({ length: n }, (_, i) => [r4(xFor(i, n)), 1] as CurvePoint)
}

export function presetBell(n: number): Curve {
  if (n <= 1) return [[0, 1]]
  const center = (n - 1) / 2
  const sigma = Math.max((n - 1) / 3, 0.5)
  return Array.from({ length: n }, (_, i) => {
    const y = Math.exp(-((i - center) ** 2) / (2 * sigma * sigma))
    return [r4(xFor(i, n)), r4(y)]
  })
}

/** Peak at the *low* end (count = min). */
export function presetLeftSkew(n: number): Curve {
  if (n <= 1) return [[0, 1]]
  // Geometric decay so the last point is ~10% of the first.
  const ratio = 0.1
  const decay = Math.pow(ratio, 1 / Math.max(n - 1, 1))
  return Array.from({ length: n }, (_, i) => {
    const y = Math.pow(decay, i)
    return [r4(xFor(i, n)), r4(y)]
  })
}

/** Peak at the *high* end (count = max). */
export function presetRightSkew(n: number): Curve {
  return presetLeftSkew(n)
    .slice()
    .reverse()
    .map(([_x, y], i, arr) => [r4(xFor(i, arr.length)), r4(y)] as CurvePoint)
}

export const PRESETS = {
  uniform: presetUniform,
  bell: presetBell,
  'left-skew': presetLeftSkew,
  'right-skew': presetRightSkew,
} as const

export type PresetName = keyof typeof PRESETS
