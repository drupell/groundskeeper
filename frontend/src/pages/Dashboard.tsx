import {
  AlertCircle,
  CalendarClock,
  Check,
  Clock,
  ExternalLink,
  Loader,
  Palmtree,
  Play,
  RefreshCw,
  Zap,
} from 'lucide-react'
import type { ReactNode } from 'react'
import { useState } from 'react'

import { Card, CardHeader } from '@/components/ui/Card'
import {
  useDashboardStatus,
  useLogs,
  useRunNow,
  useRuns,
  useTestRun,
  useVacation,
} from '@/hooks/useApi'
import type { LogEntry } from '@/lib/api'
import { cn } from '@/lib/utils'

export function DashboardPage() {
  const { status, loading, error } = useDashboardStatus()

  return (
    <div className="px-8 py-10">
      <header className="mb-8">
        <h1 className="text-3xl font-semibold tracking-tight text-[var(--color-fg)]">Dashboard</h1>
        <p className="mt-1 text-sm text-[var(--color-fg-muted)]">
          Here's today's plan and what's been happening.
        </p>
      </header>

      {error && (
        <div className="mb-6 flex items-start gap-3 rounded-lg border border-rose-900/60 bg-rose-950/40 px-4 py-3 text-sm text-rose-200">
          <AlertCircle size={18} className="mt-0.5 shrink-0" />
          <div>{error}</div>
        </div>
      )}

      {loading ? (
        <div className="text-sm text-[var(--color-fg-muted)]">One moment…</div>
      ) : status ? (
        <div className="space-y-6">
          <VacationCard
            active={status.vacation?.active ?? false}
            until={status.vacation?.until ?? null}
          />

          <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
            <StatusCard
              label="Status"
              value={status.vacation?.active ? 'Paused' : status.last_run ? 'Active' : 'Idle'}
              icon={<Zap size={18} />}
            />
            <StatusCard
              label="Timezone"
              value={status.timezone || 'UTC'}
              icon={<Clock size={18} />}
            />
            <StatusCard
              label="Last run"
              value={
                status.last_run
                  ? new Date(status.last_run.created_at).toLocaleDateString()
                  : 'Never'
              }
              icon={<Clock size={18} />}
            />
          </div>

          <TodayPlanCard />

          <TestToolsCard />

          {status.last_run && (
            <section className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface-elevated)] p-6">
              <h2 className="mb-4 text-base font-medium text-[var(--color-fg)]">Latest run</h2>
              <div className="grid grid-cols-3 gap-6">
                <Stat label="Sampled" value={status.last_run.count_sampled} accent="blue" />
                <Stat label="Placed" value={status.last_run.count_placed} accent="green" />
                <Stat label="Status" value={status.last_run.status} />
              </div>
            </section>
          )}

          <section className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface-elevated)] p-6">
            <h2 className="mb-4 text-base font-medium text-[var(--color-fg)]">Recent activity</h2>
            {status.recent_logs && status.recent_logs.length > 0 ? (
              <ul className="space-y-2">
                {status.recent_logs.slice(0, 5).map((log, idx) => (
                  <li
                    key={idx}
                    className="flex items-start gap-3 rounded-md bg-[var(--color-surface-sunk)] px-3 py-2"
                  >
                    <span
                      className={cn(
                        'mt-1.5 h-2 w-2 shrink-0 rounded-full',
                        log.status === 'ok' ? 'bg-emerald-400' : 'bg-amber-400',
                      )}
                    />
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm text-[var(--color-fg)]">
                        {log.path?.split('/').pop() ?? log.message ?? 'No detail recorded'}
                      </p>
                      <p className="mt-0.5 text-xs text-[var(--color-fg-muted)]">
                        {log.committed_at
                          ? new Date(log.committed_at).toLocaleString()
                          : log.logged_at
                            ? new Date(log.logged_at).toLocaleString()
                            : '—'}
                      </p>
                    </div>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-[var(--color-fg-muted)]">
                No commits yet — head over to Schedule to set one up.
              </p>
            )}
          </section>
        </div>
      ) : null}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Vacation Mode — the prominent home-page control
// ---------------------------------------------------------------------------

function VacationCard({ active, until }: { active: boolean; until: string | null }) {
  const { setVacation, saving, error } = useVacation()
  const [mode, setMode] = useState<'closed' | 'choosing'>('closed')
  const [scope, setScope] = useState<'indefinite' | 'until'>('indefinite')
  const [date, setDate] = useState('')

  const today = new Date().toISOString().slice(0, 10)

  if (active) {
    const resumeText = until
      ? `Resumes ${new Date(`${until}T00:00:00`).toLocaleDateString(undefined, {
          weekday: 'long',
          month: 'long',
          day: 'numeric',
        })}`
      : 'Paused until you switch it off'
    return (
      <div className="overflow-hidden rounded-xl border border-amber-800/50 bg-gradient-to-br from-amber-950/50 to-orange-950/30 p-6">
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-4">
            <div className="flex h-12 w-12 items-center justify-center rounded-full bg-amber-500/15 text-amber-300">
              <Palmtree size={22} />
            </div>
            <div>
              <p className="text-base font-medium text-amber-100">Vacation mode is on</p>
              <p className="mt-0.5 inline-flex items-center gap-1.5 text-sm text-amber-300/80">
                <CalendarClock size={13} />
                {resumeText}
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={() => setVacation(false)}
            disabled={saving}
            className="inline-flex items-center gap-1.5 rounded-md bg-amber-600 px-4 py-2 text-sm font-medium text-amber-950 transition-colors hover:bg-amber-500 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {saving ? <Loader size={14} className="animate-spin" /> : <Play size={14} />}
            Resume now
          </button>
        </div>
        {error && <p className="mt-3 text-xs text-rose-300">{error}</p>}
      </div>
    )
  }

  return (
    <Card>
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-start gap-4">
          <div className="flex h-12 w-12 items-center justify-center rounded-full bg-[var(--color-surface-sunk)] text-[var(--color-fg-muted)]">
            <Palmtree size={22} />
          </div>
          <div>
            <p className="text-base font-medium text-[var(--color-fg)]">Vacation mode</p>
            <p className="mt-0.5 max-w-md text-sm text-[var(--color-fg-muted)]">
              Pause all automated commits. While this is on, the nightly planner stays quiet and
              nothing gets scheduled.
            </p>
          </div>
        </div>
        {mode === 'closed' && (
          <button
            type="button"
            onClick={() => setMode('choosing')}
            className="shrink-0 rounded-md border border-[var(--color-border)] px-4 py-2 text-sm text-[var(--color-fg)] transition-colors hover:bg-[var(--color-surface-sunk)]"
          >
            Enable
          </button>
        )}
      </div>

      {mode === 'choosing' && (
        <div className="mt-5 space-y-4 border-t border-[var(--color-border)] pt-5">
          <div className="flex flex-wrap gap-2">
            <ScopeButton
              selected={scope === 'indefinite'}
              onClick={() => setScope('indefinite')}
              label="Until I switch it off"
            />
            <ScopeButton
              selected={scope === 'until'}
              onClick={() => setScope('until')}
              label="Until a date"
            />
          </div>

          {scope === 'until' && (
            <input
              type="date"
              min={today}
              value={date}
              onChange={(e) => setDate(e.target.value)}
              className="rounded-md border border-[var(--color-border)] bg-[var(--color-surface-sunk)] px-3 py-2 text-sm text-[var(--color-fg)] focus:border-[var(--color-brand)] focus:ring-1 focus:ring-[var(--color-brand)] focus:outline-none"
            />
          )}

          {error && <p className="text-xs text-rose-300">{error}</p>}

          <div className="flex gap-2">
            <button
              type="button"
              disabled={saving || (scope === 'until' && !date)}
              onClick={async () => {
                await setVacation(true, scope === 'until' ? date : null)
                setMode('closed')
              }}
              className="inline-flex items-center gap-1.5 rounded-md bg-[var(--color-ink)] px-4 py-2 text-sm font-medium text-[var(--color-ink-fg)] transition-colors hover:bg-[var(--color-ink-hover)] disabled:cursor-not-allowed disabled:opacity-60"
            >
              {saving && <Loader size={14} className="animate-spin" />}
              Start vacation
            </button>
            <button
              type="button"
              onClick={() => setMode('closed')}
              disabled={saving}
              className="rounded-md border border-[var(--color-border)] px-4 py-2 text-sm text-[var(--color-fg)] transition-colors hover:bg-[var(--color-surface-sunk)] disabled:cursor-not-allowed"
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </Card>
  )
}

function ScopeButton({
  selected,
  onClick,
  label,
}: {
  selected: boolean
  onClick: () => void
  label: string
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'rounded-md border px-3 py-1.5 text-sm transition-colors',
        selected
          ? 'border-[var(--color-brand)] bg-[var(--color-brand-soft)] text-[var(--color-brand)]'
          : 'border-[var(--color-border)] text-[var(--color-fg)] hover:bg-[var(--color-surface-sunk)]',
      )}
    >
      {label}
    </button>
  )
}

// ---------------------------------------------------------------------------
// Today's plan — the actual scheduled commits + whether each one landed
// ---------------------------------------------------------------------------

function fmtInTz(iso: string, tz: string): string {
  try {
    return new Intl.DateTimeFormat(undefined, {
      timeZone: tz,
      hour: '2-digit',
      minute: '2-digit',
    }).format(new Date(iso))
  } catch {
    return new Date(iso).toLocaleTimeString([], { timeStyle: 'short' })
  }
}

// Render the run's local_date relative to "today" in the same timezone, so the
// header reads "Today's plan" / "Yesterday's plan" instead of a bare ISO date
// that's easy to misread when you check the dashboard the morning after.
function relativeDayLabel(localDate: string, timezone: string): string {
  try {
    const today = new Intl.DateTimeFormat('en-CA', {
      timeZone: timezone,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
    }).format(new Date())
    if (localDate === today) return "Today's plan"
    const a = Date.parse(`${localDate}T00:00:00Z`)
    const b = Date.parse(`${today}T00:00:00Z`)
    const diff = Math.round((a - b) / 86_400_000)
    if (diff === -1) return "Yesterday's plan"
    if (diff === 1) return "Tomorrow's plan"
    if (diff < -1) return `Plan from ${-diff} days ago`
    return `Plan ${diff} days out`
  } catch {
    return 'Latest plan'
  }
}

function TodayPlanCard() {
  const { runs, loading, refetch: refetchRuns, fetching: rf } = useRuns(1)
  const { logs, refetch: refetchLogs, fetching: lf } = useLogs(100)
  const { runScheduler, running, result, error: runError } = useRunNow()
  const run = runs[0] ?? null
  const refreshing = rf || lf

  const refresh = () => {
    void Promise.all([refetchRuns(), refetchLogs()])
  }

  const times = run?.commit_times_utc ?? []
  const matched = times.map((t) => ({
    t,
    log: run ? logs.find((l) => l.run_date === run.local_date && l.scheduled_at === t) : undefined,
  }))
  const done = matched.filter((m) => m.log?.status === 'ok').length
  const failed = matched.filter((m) => m.log?.status === 'failed').length
  const pending = matched.length - done - failed

  return (
    <Card>
      <CardHeader title="Today's plan" icon={<CalendarClock size={18} />}>
        <div className="flex items-center gap-1.5">
          <button
            type="button"
            onClick={() => void runScheduler()}
            disabled={running}
            className="inline-flex items-center gap-1.5 rounded-md bg-[var(--color-ink)] px-2.5 py-1 text-xs font-medium text-[var(--color-ink-fg)] transition-colors hover:bg-[var(--color-ink-hover)] disabled:cursor-not-allowed disabled:bg-[var(--color-border)] disabled:text-[var(--color-fg-dim)]"
            title="Run the planner now — it'll set up today's schedules and start committing."
          >
            {running ? <Loader size={12} className="animate-spin" /> : <Play size={12} />}
            Run scheduler now
          </button>
          <button
            type="button"
            onClick={refresh}
            disabled={refreshing}
            className="inline-flex items-center gap-1.5 rounded-md border border-[var(--color-border)] px-2.5 py-1 text-xs text-[var(--color-fg)] transition-colors hover:bg-[var(--color-surface-sunk)] disabled:cursor-not-allowed"
          >
            <RefreshCw size={12} className={refreshing ? 'animate-spin' : ''} />
            Refresh
          </button>
        </div>
      </CardHeader>

      {runError && <p className="mb-3 text-xs text-rose-300">{runError}</p>}
      {result && !runError && (
        <p className="mb-3 inline-flex items-center gap-1.5 text-xs text-emerald-300">
          <Play size={11} />
          {result.message}
        </p>
      )}

      {loading ? (
        <p className="text-sm text-[var(--color-fg-muted)]">Loading…</p>
      ) : !run ? (
        <p className="text-sm text-[var(--color-fg-muted)]">
          Nothing's been planned yet. The daily planner runs at 12:00 UTC — or hit{' '}
          <strong className="text-[var(--color-fg)]">Run scheduler now</strong> above and we'll
          build today's plan straight away.
        </p>
      ) : (
        <>
          <div className="mb-4 flex flex-wrap items-baseline gap-x-3 gap-y-1 text-sm">
            <span className="text-[var(--color-fg)]">
              <span className="font-medium text-[var(--color-fg)]">
                {relativeDayLabel(run.local_date, run.timezone)}
              </span>{' '}
              <span className="text-[var(--color-fg-muted)]">
                · {run.local_date} ({run.timezone})
              </span>
            </span>
            <span className="text-[var(--color-fg-dim)]">·</span>
            <span className="text-[var(--color-fg-muted)]">
              <span className="text-[var(--color-fg)] tabular-nums">{run.count_placed}</span>{' '}
              {run.count_placed === run.count_sampled ? (
                'scheduled'
              ) : (
                <>
                  of{' '}
                  <span className="text-[var(--color-fg)] tabular-nums">{run.count_sampled}</span>{' '}
                  scheduled{' '}
                  <span className="text-[var(--color-fg-muted)]">
                    ({run.count_sampled - run.count_placed} already past)
                  </span>
                </>
              )}
            </span>
            {matched.length > 0 && (
              <span className="ml-auto text-xs text-[var(--color-fg-muted)] tabular-nums">
                <span className="text-emerald-400">{done} done</span>
                {failed > 0 && <span className="text-amber-400"> · {failed} failed</span>}
                {pending > 0 && (
                  <span className="text-[var(--color-fg-muted)]"> · {pending} pending</span>
                )}
              </span>
            )}
          </div>

          {matched.length === 0 ? (
            <p className="text-sm text-[var(--color-fg-muted)]">
              Nothing scheduled today
              {run.count_sampled === 0 ? ' — the distribution rolled a zero. Quiet day.' : '.'}
            </p>
          ) : (
            <ul className="space-y-1.5">
              {matched.map(({ t, log }, i) => (
                <PlanRow key={i} time={fmtInTz(t, run.timezone)} log={log} />
              ))}
            </ul>
          )}
        </>
      )}
    </Card>
  )
}

function PlanRow({ time, log }: { time: string; log?: LogEntry }) {
  const state = !log ? 'pending' : log.status === 'ok' ? 'ok' : 'failed'
  return (
    <li className="flex items-center gap-3 rounded-md bg-[var(--color-surface-sunk)] px-3 py-2 text-sm">
      <span
        className={cn(
          'inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full',
          state === 'ok' && 'bg-emerald-500/15 text-emerald-400',
          state === 'failed' && 'bg-amber-500/15 text-amber-400',
          state === 'pending' && 'bg-[var(--color-surface-elevated)] text-[var(--color-fg-muted)]',
        )}
      >
        {state === 'ok' ? (
          <Check size={12} />
        ) : state === 'failed' ? (
          <AlertCircle size={12} />
        ) : (
          <Clock size={12} />
        )}
      </span>
      <span className="w-14 shrink-0 text-[var(--color-fg)] tabular-nums">{time}</span>
      <span className="min-w-0 flex-1 truncate text-[var(--color-fg-muted)]">
        {state === 'ok' && (log?.path ?? log?.message ?? 'Committed')}
        {state === 'failed' && (
          <span className="text-amber-300">
            {log?.code ?? 'failed'}
            {log?.message ? ` — ${log.message}` : ''}
          </span>
        )}
        {state === 'pending' && <span className="text-[var(--color-fg-dim)]">pending…</span>}
      </span>
      {state === 'ok' && log?.sha && (
        <span className="shrink-0 font-mono text-xs text-[var(--color-fg-muted)]">
          {log.sha.slice(0, 7)}
        </span>
      )}
      {state === 'ok' && log?.commit_url && (
        <a
          href={log.commit_url}
          target="_blank"
          rel="noopener noreferrer"
          className="shrink-0 text-[var(--color-brand)] hover:text-[var(--color-brand-hover)]"
          aria-label="View commit on GitHub"
        >
          <ExternalLink size={13} />
        </a>
      )}
    </li>
  )
}

// ---------------------------------------------------------------------------
// Test tools — run a commit now, or preview tonight's plan
// ---------------------------------------------------------------------------

function TestToolsCard() {
  const { runTest, running, runResult, runError, previewPlan, planning, plan, planError } =
    useTestRun()

  return (
    <Card>
      <CardHeader title="Test & preview" icon={<Zap size={18} />} />
      <p className="mb-4 text-sm text-[var(--color-fg-muted)]">
        No need to wait for tonight. Fire a real commit now, or peek at what tonight's run would
        schedule — preview changes nothing.
      </p>

      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => runTest()}
          disabled={running}
          className="inline-flex items-center gap-2 rounded-md bg-[var(--color-ink)] px-4 py-2 text-sm font-medium text-[var(--color-ink-fg)] transition-colors hover:bg-[var(--color-ink-hover)] disabled:cursor-not-allowed disabled:bg-[var(--color-border)] disabled:text-[var(--color-fg-dim)]"
        >
          {running ? <Loader size={14} className="animate-spin" /> : <Play size={14} />}
          Run a test commit now
        </button>
        <button
          type="button"
          onClick={() => previewPlan()}
          disabled={planning}
          className="inline-flex items-center gap-2 rounded-md border border-[var(--color-border)] px-4 py-2 text-sm text-[var(--color-fg)] transition-colors hover:bg-[var(--color-surface-sunk)] disabled:cursor-not-allowed disabled:text-[var(--color-fg-dim)]"
        >
          {planning ? <Loader size={14} className="animate-spin" /> : <CalendarClock size={14} />}
          Preview tonight's plan
        </button>
      </div>

      {runError && <p className="mt-3 text-xs text-rose-300">{runError}</p>}
      {runResult && (
        <p className="mt-3 inline-flex items-start gap-1.5 text-xs text-emerald-300">
          <Play size={12} className="mt-0.5 shrink-0" />
          {runResult.message}
        </p>
      )}

      {planError && <p className="mt-3 text-xs text-rose-300">{planError}</p>}
      {plan && (
        <div className="mt-4 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-sunk)] p-4 text-sm">
          {plan.status === 'dry_run' ? (
            <>
              <p className="text-[var(--color-fg)]">
                Next active day: <span className="text-[var(--color-fg)]">{plan.target_date}</span>{' '}
                <span className="text-[var(--color-fg-muted)]">
                  ({plan.window?.start}–{plan.window?.end} {plan.timezone})
                </span>
              </p>
              <p className="mt-1 text-[var(--color-fg)]">
                We'd schedule{' '}
                <span className="font-semibold text-[var(--color-brand)]">
                  {plan.count_sampled}
                </span>{' '}
                commit
                {plan.count_sampled === 1 ? '' : 's'}
                {plan.planned_times_local && plan.planned_times_local.length > 0 && (
                  <>
                    {' '}
                    at{' '}
                    <span className="font-mono text-[var(--color-fg)]">
                      {plan.planned_times_local
                        .map((t) => fmtInTz(t, plan.timezone ?? 'UTC'))
                        .join(', ')}
                    </span>
                  </>
                )}
                .
              </p>
              {plan.note && (
                <p className="mt-2 text-xs text-[var(--color-fg-muted)]">{plan.note}</p>
              )}
            </>
          ) : (
            <p className="text-[var(--color-fg)]">
              Tonight's run would <span className="text-[var(--color-brand)]">skip</span>
              {plan.reason ? (
                <>
                  {' '}
                  — <span className="font-mono">{plan.reason}</span>
                </>
              ) : null}
              .
            </p>
          )}
        </div>
      )}
    </Card>
  )
}

// ---------------------------------------------------------------------------
// Status tiles
// ---------------------------------------------------------------------------

function StatusCard({ label, value, icon }: { label: string; value: string; icon: ReactNode }) {
  return (
    <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface-elevated)] p-4">
      <div className="mb-3 flex items-center justify-between">
        <p className="text-xs tracking-wide text-[var(--color-fg-muted)] uppercase">{label}</p>
        <span className="text-[var(--color-fg-muted)]">{icon}</span>
      </div>
      <p className="truncate text-xl font-semibold text-[var(--color-fg)]">{value}</p>
    </div>
  )
}

function Stat({
  label,
  value,
  accent,
}: {
  label: string
  value: string | number
  accent?: 'blue' | 'green'
}) {
  return (
    <div>
      <p className="text-xs tracking-wide text-[var(--color-fg-muted)] uppercase">{label}</p>
      <p
        className={cn(
          'mt-1 text-2xl font-semibold tabular-nums',
          accent === 'blue' && 'text-[var(--color-brand)]',
          accent === 'green' && 'text-emerald-400',
          !accent && 'text-[var(--color-fg)] capitalize',
        )}
      >
        {value}
      </p>
    </div>
  )
}
