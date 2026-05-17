// Schedule — commit-count range, the hero distribution editor, and the
// per-day active window. The range card and the weekly-window card each keep
// local edit state with explicit Save/Discard. The over-constraint banner is
// computed live from pending edits and mirrors the orchestrator's clamping
// rule (max_fit = floor(window / gap_min) + 1).

import { Check, Loader, TriangleAlert } from 'lucide-react'
import { useEffect, useState } from 'react'

import { DistributionEditor } from '@/components/DistributionEditor'
import { NumberInput } from '@/components/NumberInput'
import { Switch } from '@/components/Switch'
import { Card, CardHeader, CardSubtitle } from '@/components/ui/Card'
import { useConfig } from '@/hooks/useApi'
import type { Config, DayConfig } from '@/lib/api'
import type { Curve } from '@/lib/distribution'
import { cn } from '@/lib/utils'

const DAYS = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'] as const
type DayName = (typeof DAYS)[number]

export function SchedulePage() {
  const { config, loading, error } = useConfig()

  return (
    <div className="px-8 py-10">
      <header className="mb-8">
        <h1 className="text-3xl font-semibold tracking-tight text-white">Schedule</h1>
        <p className="mt-1 text-sm text-slate-400">
          Set how many commits land per day, shape their distribution, and pick the active window
          for each weekday.
        </p>
      </header>

      {error && (
        <div className="mb-6 rounded-lg border border-red-900/60 bg-red-950/40 px-4 py-3 text-sm text-red-200">
          {error}
        </div>
      )}

      {loading || !config ? (
        <div className="text-sm text-slate-500">Loading…</div>
      ) : (
        <div className="max-w-2xl space-y-6">
          <RangeCard config={config} />
          <Card>
            <CardHeader title="Distribution" />
            <CardSubtitle>Shape how likely each commit count is on an active day.</CardSubtitle>
            <DistributionEditorWrapper config={config} />
          </Card>
          <WeeklyWindowCard config={config} />
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Commit-count range
// ---------------------------------------------------------------------------

const COUNT_CAP = 20

function RangeCard({ config }: { config: Config }) {
  const { updateConfig } = useConfig()
  const [minText, setMinText] = useState(String(config.commit_count.min))
  const [maxText, setMaxText] = useState(String(config.commit_count.max))
  const [saving, setSaving] = useState(false)
  const [savedAt, setSavedAt] = useState<number | null>(null)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    setMinText(String(config.commit_count.min))
    setMaxText(String(config.commit_count.max))
  }, [config.commit_count.min, config.commit_count.max])

  // Clamp only when computing what we'd save: min ≥ 0, max ≥ min, both ≤ cap.
  const cMin = Math.min(COUNT_CAP, Math.max(0, parseInt(minText, 10) || 0))
  const cMax = Math.min(COUNT_CAP, Math.max(cMin, parseInt(maxText, 10) || cMin))
  const dirty = cMin !== config.commit_count.min || cMax !== config.commit_count.max
  const willClamp = minText !== String(cMin) || maxText !== String(cMax)

  const save = async () => {
    if (!dirty) return
    setSaving(true)
    setErr(null)
    try {
      await updateConfig({ commit_count: { ...config.commit_count, min: cMin, max: cMax } })
      setMinText(String(cMin))
      setMaxText(String(cMax))
      setSavedAt(Date.now())
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Save failed.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Card>
      <CardHeader title="Commits per day">
        <SaveBadge saving={saving} dirty={dirty} savedAt={savedAt} />
      </CardHeader>
      <CardSubtitle>
        The sampler draws an integer in this range each active day. Saving a new range re-points the
        distribution below.
      </CardSubtitle>
      <div className="grid grid-cols-2 gap-4">
        <NumberInput label="Minimum" value={minText} onChange={setMinText} placeholder="0" />
        <NumberInput label="Maximum" value={maxText} onChange={setMaxText} placeholder="4" />
      </div>
      {willClamp && (
        <p className="mt-3 text-xs text-slate-500">
          Saves as <span className="text-slate-300 tabular-nums">{cMin}</span>–
          <span className="text-slate-300 tabular-nums">{cMax}</span> (clamped: min ≥ 0, max ≥ min,
          ≤ {COUNT_CAP}).
        </p>
      )}
      {err && <p className="mt-3 text-xs text-red-300">{err}</p>}
      <div className="mt-4 flex justify-end gap-2">
        {dirty && (
          <button
            type="button"
            onClick={() => {
              setMinText(String(config.commit_count.min))
              setMaxText(String(config.commit_count.max))
            }}
            disabled={saving}
            className="rounded-md border border-slate-800 px-3 py-1.5 text-xs text-slate-300 transition-colors hover:bg-slate-900 disabled:cursor-not-allowed disabled:text-slate-500"
          >
            Discard
          </button>
        )}
        <button
          type="button"
          onClick={save}
          disabled={!dirty || saving}
          className="inline-flex items-center gap-1.5 rounded-md bg-sky-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-sky-500 disabled:cursor-not-allowed disabled:bg-slate-700 disabled:text-slate-400"
        >
          {saving && <Loader size={12} className="animate-spin" />}
          Save
        </button>
      </div>
    </Card>
  )
}

function DistributionEditorWrapper({ config }: { config: Config }) {
  const { updateConfig, saving } = useConfig()
  const handleCurveChange = async (curve: Curve) => {
    await updateConfig({ commit_count: { ...config.commit_count, curve } })
  }
  return (
    <DistributionEditor config={config.commit_count} onChange={handleCurveChange} saving={saving} />
  )
}

// ---------------------------------------------------------------------------
// Weekly window
// ---------------------------------------------------------------------------

function WeeklyWindowCard({ config }: { config: Config }) {
  const { updateConfig } = useConfig()
  const [days, setDays] = useState<Record<string, DayConfig>>(() => clone(config.schedule))
  const [saving, setSaving] = useState(false)
  const [savedAt, setSavedAt] = useState<number | null>(null)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    setDays(clone(config.schedule))
  }, [config.schedule])

  const dirty = JSON.stringify(days) !== JSON.stringify(config.schedule)
  const invalidDays = DAYS.filter((d) => {
    const c = days[d]
    return c?.enabled && minutesBetween(c.start, c.end) <= 0
  })
  const valid = invalidDays.length === 0

  const tight = overConstrainedDays(days, config.commit_count.max, config.gap.min_minutes)

  const patch = (day: DayName, next: Partial<DayConfig>) => {
    setDays((prev) => ({ ...prev, [day]: { ...prev[day], ...next } }))
  }

  const save = async () => {
    if (!dirty || !valid) return
    setSaving(true)
    setErr(null)
    try {
      await updateConfig({ schedule: days })
      setSavedAt(Date.now())
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Save failed.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Card>
      <CardHeader title="Weekly window">
        <SaveBadge saving={saving} dirty={dirty} savedAt={savedAt} />
      </CardHeader>
      <CardSubtitle>
        Commits are spread randomly inside each enabled day's window. Off days are dimmed.
      </CardSubtitle>

      {tight.length > 0 && (
        <div className="mb-4 flex items-start gap-2.5 rounded-md border border-amber-900/40 bg-amber-950/20 px-3 py-2.5 text-sm text-amber-200">
          <TriangleAlert size={16} className="mt-0.5 shrink-0" />
          <div>
            With up to <strong>{config.commit_count.max}</strong> commits and a{' '}
            <strong>{config.gap.min_minutes}-min</strong> minimum gap, you need at least{' '}
            <strong>{formatMinutes((config.commit_count.max - 1) * config.gap.min_minutes)}</strong>
            . That doesn't fit {tight.map((d) => titleCase(d)).join(', ')}. Those days will be{' '}
            <strong>clamped</strong> to fewer commits automatically.
          </div>
        </div>
      )}

      <div className="space-y-1.5">
        {DAYS.map((day) => {
          const c = days[day]
          if (!c) return null
          const badWindow = c.enabled && minutesBetween(c.start, c.end) <= 0
          return (
            <div
              key={day}
              className={cn(
                'flex items-center gap-4 rounded-lg border px-4 py-2.5 transition-opacity',
                c.enabled ? 'border-slate-800 bg-slate-900/50' : 'border-slate-900 opacity-50',
              )}
            >
              <Switch
                checked={c.enabled}
                onChange={(v) => patch(day, { enabled: v })}
                ariaLabel={`Toggle ${day}`}
              />
              <span className="w-24 text-sm font-medium text-slate-200 capitalize">{day}</span>
              <div className="ml-auto flex items-center gap-2">
                <TimeField
                  value={c.start}
                  onChange={(v) => patch(day, { start: v })}
                  disabled={!c.enabled}
                  invalid={badWindow}
                />
                <span className="text-slate-600">–</span>
                <TimeField
                  value={c.end}
                  onChange={(v) => patch(day, { end: v })}
                  disabled={!c.enabled}
                  invalid={badWindow}
                />
              </div>
            </div>
          )
        })}
      </div>

      {!valid && (
        <p className="mt-3 text-xs text-amber-300">
          {invalidDays.map(titleCase).join(', ')}: end time must be after start time.
        </p>
      )}
      {err && <p className="mt-3 text-xs text-red-300">{err}</p>}

      <div className="mt-4 flex justify-end gap-2">
        {dirty && (
          <button
            type="button"
            onClick={() => setDays(clone(config.schedule))}
            disabled={saving}
            className="rounded-md border border-slate-800 px-3 py-1.5 text-xs text-slate-300 transition-colors hover:bg-slate-900 disabled:cursor-not-allowed disabled:text-slate-500"
          >
            Discard
          </button>
        )}
        <button
          type="button"
          onClick={save}
          disabled={!dirty || !valid || saving}
          className="inline-flex items-center gap-1.5 rounded-md bg-sky-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-sky-500 disabled:cursor-not-allowed disabled:bg-slate-700 disabled:text-slate-400"
        >
          {saving && <Loader size={12} className="animate-spin" />}
          Save schedule
        </button>
      </div>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// Fields + helpers
// ---------------------------------------------------------------------------

function TimeField({
  value,
  onChange,
  disabled,
  invalid,
}: {
  value: string
  onChange: (v: string) => void
  disabled?: boolean
  invalid?: boolean
}) {
  return (
    <input
      type="time"
      value={value}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value)}
      className={cn(
        'rounded-md border bg-slate-900 px-2.5 py-1.5 font-mono text-sm text-slate-100 tabular-nums [color-scheme:dark] focus:ring-1 focus:outline-none',
        invalid
          ? 'border-amber-700 focus:border-amber-600 focus:ring-amber-700'
          : 'border-slate-800 focus:border-sky-700 focus:ring-sky-700',
        disabled && 'cursor-not-allowed opacity-50',
      )}
    />
  )
}

function SaveBadge({
  saving,
  dirty,
  savedAt,
}: {
  saving: boolean
  dirty: boolean
  savedAt: number | null
}) {
  if (saving) {
    return (
      <span className="inline-flex items-center gap-1.5 text-xs text-slate-500">
        <Loader size={12} className="animate-spin" /> Saving…
      </span>
    )
  }
  if (dirty) return <span className="text-xs text-amber-300">Unsaved</span>
  if (savedAt && Date.now() - savedAt < 2_500) {
    return (
      <span className="inline-flex items-center gap-1.5 text-xs text-emerald-400">
        <Check size={12} /> Saved
      </span>
    )
  }
  return null
}

function clone(s: Record<string, DayConfig>): Record<string, DayConfig> {
  return JSON.parse(JSON.stringify(s))
}

function minutesBetween(start: string, end: string): number {
  const [sh, sm] = start.split(':').map(Number)
  const [eh, em] = end.split(':').map(Number)
  if ([sh, sm, eh, em].some((n) => Number.isNaN(n))) return 0
  return eh * 60 + em - (sh * 60 + sm)
}

function overConstrainedDays(
  schedule: Record<string, DayConfig>,
  maxCommits: number,
  gapMin: number,
): DayName[] {
  if (maxCommits <= 1 || gapMin <= 0) return []
  return DAYS.filter((d) => {
    const c = schedule[d]
    if (!c?.enabled) return false
    const win = minutesBetween(c.start, c.end)
    if (win <= 0) return false
    const maxFit = Math.floor(win / gapMin) + 1
    return maxCommits > maxFit
  })
}

function formatMinutes(m: number): string {
  if (m < 60) return `${m} min`
  const h = Math.floor(m / 60)
  const rest = m % 60
  return rest === 0 ? `${h} h` : `${h} h ${rest} min`
}

function titleCase(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1)
}
