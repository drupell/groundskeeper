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
        <h1 className="text-3xl font-semibold tracking-tight text-[var(--color-fg)]">Settings</h1>
        <p className="mt-1 text-sm text-[var(--color-fg-muted)]">
          Timezone, the gap between commits, and the password you use to sign in here.
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
      setErr(e instanceof Error ? e.message : "Couldn't save — give it another go.")
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
      <CardSubtitle>We use this to plan today's commit window in your local time.</CardSubtitle>
      <div className="relative">
        <select
          value={value}
          onChange={(e) => setValue(e.target.value)}
          className="w-full appearance-none rounded-md border border-[var(--color-border)] bg-[var(--color-surface-sunk)] px-3 py-2 pr-9 text-sm text-[var(--color-fg)] focus:border-[var(--color-brand)] focus:ring-1 focus:ring-[var(--color-brand)] focus:outline-none"
        >
          {!zones.includes(value) && <option value={value}>{value}</option>}
          {zones.map((z) => (
            <option key={z} value={z}>
              {z}
            </option>
          ))}
        </select>
        <span className="pointer-events-none absolute top-1/2 right-3 -translate-y-1/2 text-[var(--color-fg-muted)]">
          ▾
        </span>
      </div>
      {preview && (
        <p className="mt-2 text-xs text-[var(--color-fg-muted)]">
          Local time in {value}:{' '}
          <span className="text-[var(--color-fg)] tabular-nums">{preview}</span>
        </p>
      )}
      {err && <p className="mt-2 text-xs text-rose-300">{err}</p>}
      <div className="mt-4 flex justify-end gap-2">
        {dirty && (
          <button
            type="button"
            onClick={() => setValue(timezone)}
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
      setErr(e instanceof Error ? e.message : "Couldn't save — give it another go.")
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
        Each day's commits land at random times inside their window — always with at least this much
        breathing room between them.
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

      {!valid && (
        <p className="mt-1 text-xs text-[var(--color-brand)]">
          Minimum can't be larger than maximum.
        </p>
      )}
      {err && <p className="mt-2 text-xs text-rose-300">{err}</p>}
      <CardFooter>
        Commits will land between{' '}
        <span className="text-[var(--color-fg)]">{formatMinutes(lo)}</span> and{' '}
        <span className="text-[var(--color-fg)]">{formatMinutes(hi)}</span> apart.
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
        <span className="text-xs tracking-wide text-[var(--color-fg-muted)] uppercase">
          {label}
        </span>
        <span className="font-mono text-sm text-[var(--color-fg)] tabular-nums">
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
      setState({ kind: 'error', message: 'Make it at least 8 characters.' })
      return
    }
    if (pw !== confirm) {
      setState({ kind: 'error', message: "Those passwords don't match." })
      return
    }
    setState({ kind: 'loading' })
    try {
      await apiClient.rotatePassword(pw)
      setPw('')
      setConfirm('')
      setState({
        kind: 'success',
        message: "Password updated — it'll be in effect the next time you sign in.",
      })
    } catch (e) {
      setState({
        kind: 'error',
        message:
          e instanceof Error ? e.message : "Couldn't change the password — give it another go.",
      })
    }
  }

  return (
    <Card>
      <CardHeader title="Dashboard password" icon={<KeyRound size={18} />} />
      <CardSubtitle>
        Changes the sign-in password for the username{' '}
        <code className="font-mono text-[var(--color-fg)]">admin</code>. Save the new one in your
        password manager before you sign out.
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
          className="inline-flex items-center gap-1.5 rounded-md bg-[var(--color-ink)] px-3 py-2 text-sm font-medium text-[var(--color-ink-fg)] transition-colors hover:bg-[var(--color-ink-hover)] disabled:cursor-not-allowed disabled:bg-[var(--color-border)] disabled:text-[var(--color-fg-dim)]"
        >
          {state.kind === 'loading' && <Loader size={14} className="animate-spin" />}
          Update password
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
      <span className="text-xs tracking-wide text-[var(--color-fg-muted)] uppercase">{label}</span>
      <input
        type="password"
        value={value}
        autoFocus={autoFocus}
        onChange={(e) => onChange(e.target.value)}
        className="mt-1 w-full rounded-md border border-[var(--color-border)] bg-[var(--color-surface-sunk)] px-3 py-2 text-sm text-[var(--color-fg)] placeholder:text-[var(--color-fg-dim)] focus:border-[var(--color-brand)] focus:ring-1 focus:ring-[var(--color-brand)] focus:outline-none"
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
          ? 'border-rose-900/40 bg-rose-950/20 text-rose-200'
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
      <span className="inline-flex items-center gap-1.5 text-xs text-[var(--color-fg-muted)]">
        <Loader size={12} className="animate-spin" /> Saving…
      </span>
    )
  }
  if (dirty) {
    return <span className="text-xs text-[var(--color-brand)]">Unsaved</span>
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
