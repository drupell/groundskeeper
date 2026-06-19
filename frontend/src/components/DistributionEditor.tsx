// The hero feature.
//
// Drag the circle handles to sculpt the per-day commit-count distribution.
// The curve below the points shows the linearly-interpolated weights the
// backend will use; the histogram and "average" readout below show the
// analytical probabilities those weights translate into.

import { Check, Loader } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'

import type { CommitCountConfig } from '@/lib/api'
import type { Curve, PresetName } from '@/lib/distribution'
import {
  PRESETS,
  clamp,
  computeProbabilities,
  curvesEqual,
  expectedValue,
  sampleCurveAtN,
} from '@/lib/distribution'
import { cn } from '@/lib/utils'

// SVG viewBox geometry. The viewBox is fixed; the actual rendered width
// scales fluidly with CSS.
const VB_W = 600
const VB_H = 240
const PAD_LEFT = 36
const PAD_RIGHT = 16
const PAD_TOP = 16
const PAD_BOTTOM = 36
const PLOT_W = VB_W - PAD_LEFT - PAD_RIGHT
const PLOT_H = VB_H - PAD_TOP - PAD_BOTTOM

const HANDLE_R = 8
const HANDLE_HIT_R = 16

interface DistributionEditorProps {
  config: CommitCountConfig
  onChange: (curve: Curve) => void | Promise<void>
  saving?: boolean
}

export function DistributionEditor({ config, onChange, saving }: DistributionEditorProps) {
  const { min, max, curve: incoming } = config
  const n = Math.max(max - min + 1, 1)

  // Local editing state. We initialize from the persisted curve, resampled
  // onto the integer positions we display. Any external change to the saved
  // curve (e.g. after a successful save round-trip) re-syncs via the
  // "adjust state during rendering" escape hatch below.
  // https://react.dev/reference/react/useState#storing-information-from-previous-renders
  const syncKey = `${n}:${JSON.stringify(incoming)}`
  const [curveState, setCurveState] = useState<{ key: string; value: Curve }>(() => ({
    key: syncKey,
    value: sampleCurveAtN(incoming, n),
  }))
  if (curveState.key !== syncKey) {
    setCurveState({ key: syncKey, value: sampleCurveAtN(incoming, n) })
  }
  const localCurve = curveState.key === syncKey ? curveState.value : sampleCurveAtN(incoming, n)
  const setLocalCurve = (updater: Curve | ((prev: Curve) => Curve)) => {
    setCurveState((prev) => ({
      key: syncKey,
      value: typeof updater === 'function' ? (updater as (p: Curve) => Curve)(prev.value) : updater,
    }))
  }
  const [dragIdx, setDragIdx] = useState<number | null>(null)
  const [savedAt, setSavedAt] = useState<number | null>(null)

  const svgRef = useRef<SVGSVGElement | null>(null)

  // ---- Layout helpers — closed over `n`, so eligible to be useMemo deps -
  const xAt = useMemo(
    () => (i: number) => PAD_LEFT + (n <= 1 ? PLOT_W / 2 : (PLOT_W * i) / (n - 1)),
    [n],
  )
  const yAt = (yNorm: number) => PAD_TOP + PLOT_H * (1 - clamp(yNorm, 0, 1))

  const pathD = useMemo(() => {
    if (localCurve.length === 0) return ''
    const pts = localCurve.map((p, i) => `${xAt(i)},${yAt(p[1])}`)
    return `M ${pts.join(' L ')}`
  }, [localCurve, xAt])

  const areaD = useMemo(() => {
    if (localCurve.length === 0) return ''
    const pts = localCurve.map((p, i) => `${xAt(i)},${yAt(p[1])}`)
    return `M ${PAD_LEFT},${PAD_TOP + PLOT_H} L ${pts.join(' L ')} L ${xAt(localCurve.length - 1)},${PAD_TOP + PLOT_H} Z`
  }, [localCurve, xAt])

  // ---- Pointer events ----------------------------------------------------
  const beginDrag = (idx: number) => (e: React.PointerEvent<SVGCircleElement>) => {
    e.preventDefault()
    e.currentTarget.setPointerCapture(e.pointerId)
    setDragIdx(idx)
  }

  const onPointerMove = (e: React.PointerEvent<SVGSVGElement>) => {
    if (dragIdx === null || !svgRef.current) return
    const pt = svgRef.current.createSVGPoint()
    pt.x = e.clientX
    pt.y = e.clientY
    const ctm = svgRef.current.getScreenCTM()
    if (!ctm) return
    const local = pt.matrixTransform(ctm.inverse())
    const yNorm = clamp(1 - (local.y - PAD_TOP) / PLOT_H, 0, 1)
    setLocalCurve((prev) => prev.map((p, i) => (i === dragIdx ? [p[0], yNorm] : p)))
  }

  const endDrag = () => {
    if (dragIdx === null) return
    setDragIdx(null)
    commit(localCurve)
  }

  // ---- Commit + preset ---------------------------------------------------
  const commit = (next: Curve) => {
    if (curvesEqual(next, incoming)) return
    void Promise.resolve(onChange(next)).then(() => setSavedAt(Date.now()))
  }

  const applyPreset = (name: PresetName) => {
    const next = PRESETS[name](n)
    setLocalCurve(next)
    commit(next)
  }

  // ---- Live preview ------------------------------------------------------
  const probs = useMemo(() => computeProbabilities(min, max, localCurve), [min, max, localCurve])
  const average = useMemo(() => expectedValue(probs), [probs])

  const totalWeight = localCurve.reduce((acc, [, y]) => acc + Math.max(0, y), 0)
  const flatWarn = totalWeight < 1e-6

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <p className="text-sm text-[var(--color-fg-muted)]">
          Drag the points or pick a shape. The curve shows how much weight each count gets.
        </p>
        <SaveBadge saving={saving} savedAt={savedAt} />
      </div>

      <div className="flex flex-wrap gap-2">
        <PresetButton label="Uniform" onClick={() => applyPreset('uniform')} />
        <PresetButton label="Bell" onClick={() => applyPreset('bell')} />
        <PresetButton label="Left-skewed" onClick={() => applyPreset('left-skew')} />
        <PresetButton label="Right-skewed" onClick={() => applyPreset('right-skew')} />
      </div>

      <div className="overflow-hidden rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-sunk)]">
        <svg
          ref={svgRef}
          viewBox={`0 0 ${VB_W} ${VB_H}`}
          className="block w-full touch-none select-none"
          onPointerMove={onPointerMove}
          onPointerUp={endDrag}
          onPointerLeave={endDrag}
          onPointerCancel={endDrag}
          role="application"
          aria-label="Commit-count distribution curve editor"
        >
          {/* Horizontal gridlines at 25/50/75/100% */}
          {[0, 0.25, 0.5, 0.75, 1].map((g) => (
            <line
              key={g}
              x1={PAD_LEFT}
              x2={PAD_LEFT + PLOT_W}
              y1={yAt(g)}
              y2={yAt(g)}
              stroke="var(--color-border)"
              strokeWidth={1}
              strokeDasharray={g === 0 || g === 1 ? '' : '2 4'}
            />
          ))}

          {/* y-axis hint labels */}
          <text
            x={PAD_LEFT - 8}
            y={yAt(1)}
            fontSize={10}
            textAnchor="end"
            dominantBaseline="middle"
            fill="var(--color-fg-dim)"
          >
            more
          </text>
          <text
            x={PAD_LEFT - 8}
            y={yAt(0)}
            fontSize={10}
            textAnchor="end"
            dominantBaseline="middle"
            fill="var(--color-fg-dim)"
          >
            less
          </text>

          {/* Area under the curve */}
          <path d={areaD} fill="var(--color-brand-soft)" />
          {/* The curve itself */}
          <path
            d={pathD}
            fill="none"
            stroke="var(--color-brand)"
            strokeWidth={2}
            strokeLinejoin="round"
            strokeLinecap="round"
          />

          {/* Control points */}
          {localCurve.map(([, y], i) => {
            const cx = xAt(i)
            const cy = yAt(y)
            const isActive = dragIdx === i
            return (
              <g key={i}>
                {/* Visible handle */}
                <circle
                  cx={cx}
                  cy={cy}
                  r={HANDLE_R}
                  fill="var(--color-surface-elevated)"
                  stroke="var(--color-brand)"
                  strokeWidth={isActive ? 3 : 2}
                  className={cn(isActive && 'drop-shadow-[0_0_8px_rgba(245,158,11,0.6)]')}
                />
                {/* Larger transparent hit target */}
                <circle
                  cx={cx}
                  cy={cy}
                  r={HANDLE_HIT_R}
                  fill="transparent"
                  className="cursor-grab active:cursor-grabbing"
                  onPointerDown={beginDrag(i)}
                />
                {/* x-axis count label */}
                <text
                  x={cx}
                  y={PAD_TOP + PLOT_H + 18}
                  fontSize={11}
                  textAnchor="middle"
                  fill={isActive ? 'var(--color-brand)' : 'var(--color-fg-muted)'}
                  className="tabular-nums"
                >
                  {min + i}
                </text>
              </g>
            )
          })}
        </svg>
      </div>

      {flatWarn && (
        <div className="rounded-md border border-amber-900/40 bg-amber-950/20 px-3 py-2 text-xs text-amber-200">
          The curve is flat at zero, so every count's equally likely. Drag a point up to bias the
          distribution.
        </div>
      )}

      <Histogram min={min} max={max} probs={probs} average={average} />
    </div>
  )
}

// ---------------------------------------------------------------------------
// Subcomponents
// ---------------------------------------------------------------------------

function PresetButton({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="rounded-md border border-[var(--color-border)] bg-[var(--color-surface-elevated)] px-3 py-1.5 text-xs font-medium text-[var(--color-fg)] transition-colors hover:border-[var(--color-border-strong)] hover:bg-[var(--color-surface-sunk)] hover:text-[var(--color-fg)]"
    >
      {label}
    </button>
  )
}

function SaveBadge({ saving, savedAt }: { saving?: boolean; savedAt: number | null }) {
  // "Saved" badge flashes for 2s after each save. We track the savedAt the
  // badge is currently showing for; a fresh savedAt flips it on during
  // render, and a timer flips it back off.
  const [shownFor, setShownFor] = useState<number | null>(null)
  const showSaved = shownFor !== null && shownFor === savedAt

  // Adjust state during rendering when a new save lands — see
  // https://react.dev/reference/react/useState#storing-information-from-previous-renders
  // shownFor === -savedAt encodes "this savedAt has already timed out", so
  // we don't re-trigger the badge after the timer flips it off.
  if (savedAt !== null && shownFor !== savedAt && shownFor !== -savedAt) {
    setShownFor(savedAt)
  }

  useEffect(() => {
    if (savedAt === null) return
    const t = window.setTimeout(() => setShownFor(-savedAt), 2_000)
    return () => window.clearTimeout(t)
  }, [savedAt])

  if (saving) {
    return (
      <span className="inline-flex items-center gap-1.5 text-xs text-[var(--color-fg-muted)]">
        <Loader size={12} className="animate-spin" /> Saving…
      </span>
    )
  }
  if (showSaved) {
    return (
      <span className="inline-flex items-center gap-1.5 text-xs text-emerald-400">
        <Check size={12} /> Saved
      </span>
    )
  }
  return null
}

function Histogram({
  min,
  max,
  probs,
  average,
}: {
  min: number
  max: number
  probs: { count: number; prob: number }[]
  average: number
}) {
  const peak = Math.max(...probs.map((p) => p.prob), 0.001)
  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-sunk)] p-4">
      <div className="mb-3 flex items-baseline justify-between">
        <p className="text-sm text-[var(--color-fg-muted)]">
          On active days, you'll average{' '}
          <span className="font-semibold text-[var(--color-brand)] tabular-nums">
            ~{average.toFixed(2)}
          </span>{' '}
          commits a day.
        </p>
        <p className="text-xs text-[var(--color-fg-muted)] tabular-nums">
          range {min}–{max}
        </p>
      </div>
      <div
        className="grid items-end gap-2"
        style={{ gridTemplateColumns: `repeat(${probs.length}, minmax(0, 1fr))` }}
      >
        {probs.map(({ count, prob }) => {
          const pct = Math.round(prob * 100)
          const heightPct = (prob / peak) * 100
          return (
            <div key={count} className="flex flex-col items-center gap-1">
              <div className="relative h-24 w-full overflow-hidden rounded-sm bg-[var(--color-surface-elevated)]">
                <div
                  className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-[var(--color-brand)]/70 to-[var(--color-brand)]/40 transition-[height] duration-200 ease-out"
                  style={{ height: `${heightPct}%` }}
                />
              </div>
              <div className="text-[10px] font-medium text-[var(--color-fg)] tabular-nums">
                {pct}%
              </div>
              <div className="text-[10px] text-[var(--color-fg-muted)] tabular-nums">{count}</div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
