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
        <h1 className="text-3xl font-semibold tracking-tight text-[var(--color-fg)]">Schedule</h1>
        <p className="mt-1 text-sm text-[var(--color-fg-muted)]">
          Decide how many commits land each day, shape the distribution, and choose when each
          weekday is in play.
        </p>
      </header>

      {error && (
        <div className="mb-6 rounded-lg border border-rose-900/60 bg-rose-950/40 px-4 py-3 text-sm text-rose-200">
          {error}
        </div>
      )}

      {loading || !config ? (
        <div className="text-sm text-[var(--color-fg-muted)]">One moment…</div>
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
  // Reset-on-change during render: when the saved config.commit_count changes
  // upstream, we discard local edits and re-seed from the new values. The
  // "key" fields track which saved snapshot the local text reflects.
  const [textState, setTextState] = useState({
    key: { min: config.commit_count.min, max: config.commit_count.max },
    min: String(config.commit_count.min),
    max: String(config.commit_count.max),
  })
  if (
    textState.key.min !== config.commit_count.min ||
    textState.key.max !== config.commit_count.max
  ) {
    setTextState({
      key: { min: config.commit_count.min, max: config.commit_count.max },
      min: String(config.commit_count.min),
      max: String(config.commit_count.max),
    })
  }
  const minText = textState.min
  const maxText = textState.max
  const setMinText = (v: string) => setTextState((prev) => ({ ...prev, min: v }))
  const setMaxText = (v: string) => setTextState((prev) => ({ ...prev, max: v }))
  const [saving, setSaving] = useState(false)
  const [savedAt, setSavedAt] = useState<number | null>(null)
  const [err, setErr] = useState<string | null>(null)

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
      setErr(e instanceof Error ? e.message : "Couldn't save — give it another go.")
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
        Each active day, we pick a whole number in this range. Saving a new range resets the
        distribution below to match.
      </CardSubtitle>
      <div className="grid grid-cols-2 gap-4">
        <NumberInput label="Minimum" value={minText} onChange={setMinText} placeholder="0" />
        <NumberInput label="Maximum" value={maxText} onChange={setMaxText} placeholder="4" />
      </div>
      {willClamp && (
        <p className="mt-3 text-xs text-[var(--color-fg-muted)]">
          We'll save this as <span className="text-[var(--color-fg)] tabular-nums">{cMin}</span>–
          <span className="text-[var(--color-fg)] tabular-nums">{cMax}</span> (min ≥ 0, max ≥ min,
          both ≤ {COUNT_CAP}).
        </p>
      )}
      {err && <p className="mt-3 text-xs text-rose-300">{err}</p>}
      <div className="mt-4 flex justify-end gap-2">
        {dirty && (
          <button
            type="button"
            onClick={() => {
              setMinText(String(config.commit_count.min))
              setMaxText(String(config.commit_count.max))
            }}
            disabled={saving}
            className="rounded-md border border-[var(--color-border)] px-3 py-1.5 text-xs text-[var(--color-fg)] transition-colors hover:bg-[var(--color-surface-sunk)] disabled:cursor-not-allowed disabled:text-[var(--color-fg-dim)]"
          >
            Discard
          </button>
        )}
        <button
          type="button"
          onClick={save}
          disabled={!dirty || saving}
          className="inline-flex items-center gap-1.5 rounded-md bg-[var(--color-ink)] px-3 py-1.5 text-xs font-medium text-[var(--color-ink-fg)] transition-colors hover:bg-[var(--color-ink-hover)] disabled:cursor-not-allowed disabled:bg-[var(--color-border)] disabled:text-[var(--color-fg-dim)]"
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
  // Reset-on-change during render: when the saved schedule changes upstream
  // (identity comparison — useConfig hands back a new object each save) we
  // discard local edits and re-seed from the new snapshot.
  const [daysState, setDaysState] = useState<{
    key: Record<string, DayConfig>
    days: Record<string, DayConfig>
  }>(() => ({ key: config.schedule, days: clone(config.schedule) }))
  if (daysState.key !== config.schedule) {
    setDaysState({ key: config.schedule, days: clone(config.schedule) })
  }
  const days = daysState.days
  const setDays = (
    next:
      | Record<string, DayConfig>
      | ((prev: Record<string, DayConfig>) => Record<string, DayConfig>),
  ) =>
    setDaysState((prev) => ({
      ...prev,
      days: typeof next === 'function' ? next(prev.days) : next,
    }))
  const [saving, setSaving] = useState(false)
  const [savedAt, setSavedAt] = useState<number | null>(null)
  const [err, setErr] = useState<string | null>(null)

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
      setErr(e instanceof Error ? e.message : "Couldn't save the schedule — give it another go.")
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
        Commits land at random times inside each active day's window. Days that are off are dimmed.
      </CardSubtitle>

      {tight.length > 0 && (
        <div className="mb-4 flex items-start gap-2.5 rounded-md border border-amber-900/40 bg-amber-950/20 px-3 py-2.5 text-sm text-amber-200">
          <TriangleAlert size={16} className="mt-0.5 shrink-0" />
          <div>
            With up to <strong>{config.commit_count.max}</strong> commits and a{' '}
            <strong>{config.gap.min_minutes}-min</strong> minimum gap, you'd need at least{' '}
            <strong>{formatMinutes((config.commit_count.max - 1) * config.gap.min_minutes)}</strong>
            . That won't fit on {tight.map((d) => titleCase(d)).join(', ')} — those days will be{' '}
            <strong>trimmed</strong> to fewer commits automatically.
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
                c.enabled
                  ? 'border-[var(--color-border)] bg-[var(--color-surface-sunk)]'
                  : 'border-[var(--color-border)] opacity-50',
              )}
            >
              <Switch
                checked={c.enabled}
                onChange={(v) => patch(day, { enabled: v })}
                ariaLabel={`Toggle ${day}`}
              />
              <span className="w-24 text-sm font-medium text-[var(--color-fg)] capitalize">
                {day}
              </span>
              <div className="ml-auto flex items-center gap-2">
                <TimeField
                  value={c.start}
                  onChange={(v) => patch(day, { start: v })}
                  disabled={!c.enabled}
                  invalid={badWindow}
                />
                <span className="text-[var(--color-fg-dim)]">–</span>
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
        <p className="mt-3 text-xs text-[var(--color-brand)]">
          {invalidDays.map(titleCase).join(', ')}: the end time needs to be after the start.
        </p>
      )}
      {err && <p className="mt-3 text-xs text-rose-300">{err}</p>}

      <div className="mt-4 flex justify-end gap-2">
        {dirty && (
          <button
            type="button"
            onClick={() => setDays(clone(config.schedule))}
            disabled={saving}
            className="rounded-md border border-[var(--color-border)] px-3 py-1.5 text-xs text-[var(--color-fg)] transition-colors hover:bg-[var(--color-surface-sunk)] disabled:cursor-not-allowed disabled:text-[var(--color-fg-dim)]"
          >
            Discard
          </button>
        )}
        <button
          type="button"
          onClick={save}
          disabled={!dirty || !valid || saving}
          className="inline-flex items-center gap-1.5 rounded-md bg-[var(--color-ink)] px-3 py-1.5 text-xs font-medium text-[var(--color-ink-fg)] transition-colors hover:bg-[var(--color-ink-hover)] disabled:cursor-not-allowed disabled:bg-[var(--color-border)] disabled:text-[var(--color-fg-dim)]"
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
        'rounded-md border bg-[var(--color-surface-sunk)] px-2.5 py-1.5 font-mono text-sm text-[var(--color-fg)] tabular-nums focus:ring-1 focus:outline-none',
        invalid
          ? 'border-[var(--color-brand)] focus:border-[var(--color-brand-hover)] focus:ring-[var(--color-brand)]'
          : 'border-[var(--color-border)] focus:border-[var(--color-brand)] focus:ring-[var(--color-brand)]',
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
  // Timer-driven "Saved" indicator — responds to savedAt changing rather than
  // recomputing freshness from Date.now() in render (which would be impure).
  const [showSaved, setShowSaved] = useState(false)
  useEffect(() => {
    if (!savedAt) return
    // eslint-disable-next-line react-hooks/set-state-in-effect -- responding to an external event (savedAt timestamp change), not deriving state from props
    setShowSaved(true)
    const t = window.setTimeout(() => setShowSaved(false), 2_500)
    return () => window.clearTimeout(t)
  }, [savedAt])

  if (saving) {
    return (
      <span className="inline-flex items-center gap-1.5 text-xs text-[var(--color-fg-muted)]">
        <Loader size={12} className="animate-spin" /> Saving…
      </span>
    )
  }
  if (dirty) return <span className="text-xs text-[var(--color-brand)]">Unsaved</span>
  if (showSaved) {
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
