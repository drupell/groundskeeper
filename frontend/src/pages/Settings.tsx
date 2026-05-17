// Settings — timezone, commit gap, dashboard password.
//
// Timezone and gap PATCH `config`; password rotation calls a separate endpoint
// (POST /password) and doesn't touch DynamoDB. The three cards are
// independently saveable.

import { AlertCircle, Check, Clock, KeyRound, Loader, Timer } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'

import { Slider } from '@/components/Slider'
import { Card, CardFooter, CardHeader, CardSubtitle } from '@/components/ui/Card'
import { useConfig } from '@/hooks/useApi'
import { apiClient } from '@/lib/api'
import { cn } from '@/lib/utils'

export function SettingsPage() {
  const { config, loading, error } = useConfig()

  return (
    <div className="px-8 py-10">
      <header className="mb-8">
        <h1 className="text-3xl font-semibold tracking-tight text-white">Settings</h1>
        <p className="mt-1 text-sm text-slate-400">
          Timezone, commit gap, and the dashboard's basic-auth password.
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
          <TimezoneCard timezone={config.timezone} />
          <GapCard min={config.gap.min_minutes} max={config.gap.max_minutes} />
          <PasswordCard />
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Timezone
// ---------------------------------------------------------------------------

function listTimezones(): string[] {
  // Modern browsers (Chrome 99+, Safari 15.4+, Firefox 95+) expose this.
  const intl = Intl as typeof Intl & { supportedValuesOf?: (k: string) => string[] }
  if (typeof intl.supportedValuesOf === 'function') {
    try {
      return intl.supportedValuesOf('timeZone')
    } catch {
      /* fall through */
    }
  }
  return [
    'UTC',
    'America/Los_Angeles',
    'America/Denver',
    'America/Chicago',
    'America/New_York',
    'America/Toronto',
    'America/Sao_Paulo',
    'Europe/London',
    'Europe/Berlin',
    'Europe/Paris',
    'Europe/Amsterdam',
    'Europe/Stockholm',
    'Africa/Cairo',
    'Asia/Dubai',
    'Asia/Kolkata',
    'Asia/Singapore',
    'Asia/Hong_Kong',
    'Asia/Tokyo',
    'Asia/Seoul',
    'Australia/Sydney',
    'Pacific/Auckland',
  ]
}

function TimezoneCard({ timezone }: { timezone: string }) {
  const { updateConfig } = useConfig()
  const [value, setValue] = useState(timezone)
  const [saving, setSaving] = useState(false)
  const [savedAt, setSavedAt] = useState<number | null>(null)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    setValue(timezone)
  }, [timezone])

  const zones = useMemo(listTimezones, [])
  const dirty = value !== timezone
  const valid = zones.includes(value) || zones.length === 0

  const save = async () => {
    if (!dirty || !valid) return
    setSaving(true)
    setErr(null)
    try {
      await updateConfig({ timezone: value })
      setSavedAt(Date.now())
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Save failed.')
    } finally {
      setSaving(false)
    }
  }

  // Show the local time in the candidate timezone as a sanity preview.
  const preview = useMemo(() => {
    try {
      return new Intl.DateTimeFormat(undefined, {
        timeZone: value,
        hour: '2-digit',
        minute: '2-digit',
        weekday: 'short',
      }).format(new Date())
    } catch {
      return null
    }
  }, [value])

  return (
    <Card>
      <CardHeader title="Timezone" icon={<Clock size={18} />}>
        <SaveBadge saving={saving} dirty={dirty} savedAt={savedAt} />
      </CardHeader>
      <CardSubtitle>
        The orchestrator uses this when planning today's commit window in local time.
      </CardSubtitle>
      <div className="relative">
        <select
          value={value}
          onChange={(e) => setValue(e.target.value)}
          className="w-full appearance-none rounded-md border border-slate-800 bg-slate-900 px-3 py-2 pr-9 text-sm text-slate-100 focus:border-sky-700 focus:ring-1 focus:ring-sky-700 focus:outline-none"
        >
          {!zones.includes(value) && <option value={value}>{value}</option>}
          {zones.map((z) => (
            <option key={z} value={z}>
              {z}
            </option>
          ))}
        </select>
        <span className="pointer-events-none absolute top-1/2 right-3 -translate-y-1/2 text-slate-500">
          ▾
        </span>
      </div>
      {preview && (
        <p className="mt-2 text-xs text-slate-500">
          Local time in {value}: <span className="text-slate-300 tabular-nums">{preview}</span>
        </p>
      )}
      {err && <p className="mt-2 text-xs text-red-300">{err}</p>}
      <div className="mt-4 flex justify-end gap-2">
        {dirty && (
          <button
            type="button"
            onClick={() => setValue(timezone)}
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
          Save
        </button>
      </div>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// Commit gap — two sliders, clamped 0..720 minutes
// ---------------------------------------------------------------------------

const GAP_MAX = 480 // 8 hours — longer than any realistic active day

function GapCard({ min, max }: { min: number; max: number }) {
  const { updateConfig } = useConfig()
  const [lo, setLo] = useState(min)
  const [hi, setHi] = useState(max)
  const [saving, setSaving] = useState(false)
  const [savedAt, setSavedAt] = useState<number | null>(null)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    setLo(min)
    setHi(max)
  }, [min, max])

  const dirty = lo !== min || hi !== max
  const valid = lo >= 0 && hi <= GAP_MAX && lo <= hi

  const commit = async (nextLo: number, nextHi: number) => {
    if (nextLo === min && nextHi === max) return
    setSaving(true)
    setErr(null)
    try {
      await updateConfig({ gap: { min_minutes: nextLo, max_minutes: nextHi } })
      setSavedAt(Date.now())
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Save failed.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Card>
      <CardHeader title="Time between commits" icon={<Timer size={18} />}>
        <SaveBadge saving={saving} dirty={dirty} savedAt={savedAt} />
      </CardHeader>
      <CardSubtitle>
        The orchestrator places each day's commits at random times inside the schedule window, with
        at least this much space between consecutive ones.
      </CardSubtitle>

      <SliderRow
        label="Minimum"
        value={lo}
        onChange={(v) => setLo(Math.min(v, hi))}
        onCommit={(v) => commit(Math.min(v, hi), hi)}
        max={GAP_MAX}
      />
      <SliderRow
        label="Maximum"
        value={hi}
        onChange={(v) => setHi(Math.max(v, lo))}
        onCommit={(v) => commit(lo, Math.max(v, lo))}
        max={GAP_MAX}
      />

      {!valid && <p className="mt-1 text-xs text-amber-300">Minimum must be ≤ maximum.</p>}
      {err && <p className="mt-2 text-xs text-red-300">{err}</p>}
      <CardFooter>
        Plan: gaps between <span className="text-slate-400">{formatMinutes(lo)}</span> and{' '}
        <span className="text-slate-400">{formatMinutes(hi)}</span>.
      </CardFooter>
    </Card>
  )
}

function SliderRow({
  label,
  value,
  onChange,
  onCommit,
  max,
}: {
  label: string
  value: number
  onChange: (v: number) => void
  onCommit: (v: number) => void
  max: number
}) {
  return (
    <div className="mb-4">
      <div className="mb-2 flex items-baseline justify-between">
        <span className="text-xs tracking-wide text-slate-500 uppercase">{label}</span>
        <span className="font-mono text-sm text-slate-200 tabular-nums">
          {formatMinutes(value)}
        </span>
      </div>
      <Slider
        value={value}
        min={0}
        max={max}
        step={5}
        ariaLabel={`${label} gap in minutes`}
        onChange={onChange}
        onCommit={onCommit}
      />
    </div>
  )
}

function formatMinutes(m: number): string {
  if (m < 60) return `${m} min`
  const h = Math.floor(m / 60)
  const rest = m % 60
  return rest === 0 ? `${h} h` : `${h} h ${rest} min`
}

// ---------------------------------------------------------------------------
// Dashboard password rotation
// ---------------------------------------------------------------------------

function PasswordCard() {
  const [pw, setPw] = useState('')
  const [confirm, setConfirm] = useState('')
  const [state, setState] = useState<UiState>({ kind: 'idle' })

  const rotate = async () => {
    if (pw.length < 8) {
      setState({ kind: 'error', message: 'Password must be at least 8 characters.' })
      return
    }
    if (pw !== confirm) {
      setState({ kind: 'error', message: "Passwords don't match." })
      return
    }
    setState({ kind: 'loading' })
    try {
      await apiClient.rotatePassword(pw)
      setPw('')
      setConfirm('')
      setState({
        kind: 'success',
        message: 'Password rotated. The new password takes effect immediately for new sessions.',
      })
    } catch (e) {
      setState({ kind: 'error', message: e instanceof Error ? e.message : 'Failed.' })
    }
  }

  return (
    <Card>
      <CardHeader title="Dashboard password" icon={<KeyRound size={18} />} />
      <CardSubtitle>
        Rotates the Amplify basic-auth credentials for the username{' '}
        <code className="font-mono text-slate-400">admin</code>. Update your password manager before
        you sign out.
      </CardSubtitle>

      {state.kind === 'error' && (
        <Banner kind="error">
          <AlertCircle size={16} className="mt-0.5 shrink-0" />
          <span>{state.message}</span>
        </Banner>
      )}
      {state.kind === 'success' && (
        <Banner kind="success">
          <Check size={16} className="mt-0.5 shrink-0" />
          <span>{state.message}</span>
        </Banner>
      )}

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <PasswordInput label="New password" value={pw} onChange={setPw} autoFocus={false} />
        <PasswordInput label="Confirm" value={confirm} onChange={setConfirm} autoFocus={false} />
      </div>

      <div className="mt-4 flex justify-end">
        <button
          type="button"
          onClick={rotate}
          disabled={state.kind === 'loading' || !pw || !confirm}
          className="inline-flex items-center gap-1.5 rounded-md bg-sky-600 px-3 py-2 text-sm font-medium text-white transition-colors hover:bg-sky-500 disabled:cursor-not-allowed disabled:bg-slate-700 disabled:text-slate-400"
        >
          {state.kind === 'loading' && <Loader size={14} className="animate-spin" />}
          Rotate password
        </button>
      </div>
    </Card>
  )
}

function PasswordInput({
  label,
  value,
  onChange,
  autoFocus,
}: {
  label: string
  value: string
  onChange: (v: string) => void
  autoFocus?: boolean
}) {
  return (
    <label className="block">
      <span className="text-xs tracking-wide text-slate-500 uppercase">{label}</span>
      <input
        type="password"
        value={value}
        autoFocus={autoFocus}
        onChange={(e) => onChange(e.target.value)}
        className="mt-1 w-full rounded-md border border-slate-800 bg-slate-900 px-3 py-2 text-sm text-slate-100 placeholder:text-slate-600 focus:border-sky-700 focus:ring-1 focus:ring-sky-700 focus:outline-none"
      />
    </label>
  )
}

type UiState =
  | { kind: 'idle' }
  | { kind: 'loading' }
  | { kind: 'success'; message: string }
  | { kind: 'error'; message: string }

function Banner({ kind, children }: { kind: 'error' | 'success'; children: React.ReactNode }) {
  return (
    <div
      className={cn(
        'mb-4 flex items-start gap-2 rounded-md border px-3 py-2 text-sm',
        kind === 'error'
          ? 'border-red-900/40 bg-red-950/20 text-red-200'
          : 'border-emerald-900/40 bg-emerald-950/20 text-emerald-200',
      )}
    >
      {children}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Shared
// ---------------------------------------------------------------------------

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
  if (dirty) {
    return <span className="text-xs text-amber-300">Unsaved</span>
  }
  if (savedAt && Date.now() - savedAt < 2_500) {
    return (
      <span className="inline-flex items-center gap-1.5 text-xs text-emerald-400">
        <Check size={12} /> Saved
      </span>
    )
  }
  return null
}
