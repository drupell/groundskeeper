import { AlertCircle, CalendarClock, Clock, Loader, Palmtree, Play, Zap } from 'lucide-react'
import type { ReactNode } from 'react'
import { useState } from 'react'

import { Card, CardHeader } from '@/components/ui/Card'
import { useDashboardStatus, useTestRun, useVacation } from '@/hooks/useApi'
import { cn } from '@/lib/utils'

export function DashboardPage() {
  const { status, loading, error } = useDashboardStatus()

  return (
    <div className="px-8 py-10">
      <header className="mb-8">
        <h1 className="text-3xl font-semibold tracking-tight text-white">Dashboard</h1>
        <p className="mt-1 text-sm text-slate-400">Today's plan and recent activity.</p>
      </header>

      {error && (
        <div className="mb-6 flex items-start gap-3 rounded-lg border border-red-900/60 bg-red-950/40 px-4 py-3 text-sm text-red-200">
          <AlertCircle size={18} className="mt-0.5 shrink-0" />
          <div>{error}</div>
        </div>
      )}

      {loading ? (
        <div className="text-sm text-slate-500">Loading…</div>
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

          <TestToolsCard />

          {status.last_run && (
            <section className="rounded-xl border border-slate-800 bg-slate-950 p-6">
              <h2 className="mb-4 text-base font-medium text-white">Latest run</h2>
              <div className="grid grid-cols-3 gap-6">
                <Stat label="Sampled" value={status.last_run.count_sampled} accent="blue" />
                <Stat label="Placed" value={status.last_run.count_placed} accent="green" />
                <Stat label="Status" value={status.last_run.status} />
              </div>
            </section>
          )}

          <section className="rounded-xl border border-slate-800 bg-slate-950 p-6">
            <h2 className="mb-4 text-base font-medium text-white">Recent activity</h2>
            {status.recent_logs && status.recent_logs.length > 0 ? (
              <ul className="space-y-2">
                {status.recent_logs.slice(0, 5).map((log, idx) => (
                  <li
                    key={idx}
                    className="flex items-start gap-3 rounded-md bg-slate-900/60 px-3 py-2"
                  >
                    <span
                      className={cn(
                        'mt-1.5 h-2 w-2 shrink-0 rounded-full',
                        log.status === 'ok' ? 'bg-emerald-400' : 'bg-amber-400',
                      )}
                    />
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm text-slate-200">
                        {log.path?.split('/').pop() ?? log.message ?? '(no detail)'}
                      </p>
                      <p className="mt-0.5 text-xs text-slate-500">
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
              <p className="text-sm text-slate-500">
                No commits yet. Schedule something on the Schedule page.
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
      : 'Paused until you turn it back on'
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
            className="inline-flex items-center gap-1.5 rounded-md bg-amber-500 px-4 py-2 text-sm font-medium text-amber-950 transition-colors hover:bg-amber-400 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {saving ? <Loader size={14} className="animate-spin" /> : <Play size={14} />}
            Resume now
          </button>
        </div>
        {error && <p className="mt-3 text-xs text-red-300">{error}</p>}
      </div>
    )
  }

  return (
    <Card>
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-start gap-4">
          <div className="flex h-12 w-12 items-center justify-center rounded-full bg-slate-800 text-slate-400">
            <Palmtree size={22} />
          </div>
          <div>
            <p className="text-base font-medium text-white">Vacation Mode</p>
            <p className="mt-0.5 max-w-md text-sm text-slate-400">
              Pause all automated commits. The nightly planner exits cleanly while this is on — no
              schedules are created.
            </p>
          </div>
        </div>
        {mode === 'closed' && (
          <button
            type="button"
            onClick={() => setMode('choosing')}
            className="shrink-0 rounded-md border border-slate-800 px-4 py-2 text-sm text-slate-200 transition-colors hover:bg-slate-900"
          >
            Enable
          </button>
        )}
      </div>

      {mode === 'choosing' && (
        <div className="mt-5 space-y-4 border-t border-slate-800 pt-5">
          <div className="flex flex-wrap gap-2">
            <ScopeButton
              selected={scope === 'indefinite'}
              onClick={() => setScope('indefinite')}
              label="Until I turn it off"
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
              className="rounded-md border border-slate-800 bg-slate-900 px-3 py-2 text-sm text-slate-100 [color-scheme:dark] focus:border-sky-700 focus:ring-1 focus:ring-sky-700 focus:outline-none"
            />
          )}

          {error && <p className="text-xs text-red-300">{error}</p>}

          <div className="flex gap-2">
            <button
              type="button"
              disabled={saving || (scope === 'until' && !date)}
              onClick={async () => {
                await setVacation(true, scope === 'until' ? date : null)
                setMode('closed')
              }}
              className="inline-flex items-center gap-1.5 rounded-md bg-amber-500 px-4 py-2 text-sm font-medium text-amber-950 transition-colors hover:bg-amber-400 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {saving && <Loader size={14} className="animate-spin" />}
              Start vacation
            </button>
            <button
              type="button"
              onClick={() => setMode('closed')}
              disabled={saving}
              className="rounded-md border border-slate-800 px-4 py-2 text-sm text-slate-300 transition-colors hover:bg-slate-900 disabled:cursor-not-allowed"
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
          ? 'border-sky-700 bg-sky-950/40 text-sky-200'
          : 'border-slate-800 text-slate-300 hover:bg-slate-900',
      )}
    >
      {label}
    </button>
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
      <p className="mb-4 text-sm text-slate-400">
        Don't wait on the nightly cron. Fire a real commit right now, or preview what tonight's run
        would schedule without changing anything.
      </p>

      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => runTest()}
          disabled={running}
          className="inline-flex items-center gap-2 rounded-md bg-sky-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-sky-500 disabled:cursor-not-allowed disabled:bg-slate-700 disabled:text-slate-400"
        >
          {running ? <Loader size={14} className="animate-spin" /> : <Play size={14} />}
          Run a test commit now
        </button>
        <button
          type="button"
          onClick={() => previewPlan()}
          disabled={planning}
          className="inline-flex items-center gap-2 rounded-md border border-slate-800 px-4 py-2 text-sm text-slate-200 transition-colors hover:bg-slate-900 disabled:cursor-not-allowed disabled:text-slate-500"
        >
          {planning ? <Loader size={14} className="animate-spin" /> : <CalendarClock size={14} />}
          Preview tonight's plan
        </button>
      </div>

      {runError && <p className="mt-3 text-xs text-red-300">{runError}</p>}
      {runResult && (
        <p className="mt-3 inline-flex items-start gap-1.5 text-xs text-emerald-300">
          <Play size={12} className="mt-0.5 shrink-0" />
          {runResult.message}
        </p>
      )}

      {planError && <p className="mt-3 text-xs text-red-300">{planError}</p>}
      {plan && (
        <div className="mt-4 rounded-lg border border-slate-800 bg-slate-900/50 p-4 text-sm">
          {plan.status === 'dry_run' ? (
            <>
              <p className="text-slate-300">
                Next active day: <span className="text-white">{plan.target_date}</span>{' '}
                <span className="text-slate-500">
                  ({plan.window?.start}–{plan.window?.end} {plan.timezone})
                </span>
              </p>
              <p className="mt-1 text-slate-300">
                Would schedule{' '}
                <span className="font-semibold text-sky-300">{plan.count_sampled}</span> commit
                {plan.count_sampled === 1 ? '' : 's'}
                {plan.planned_times_local && plan.planned_times_local.length > 0 && (
                  <>
                    {' '}
                    at{' '}
                    <span className="font-mono text-slate-200">
                      {plan.planned_times_local
                        .map((t) => new Date(t).toLocaleTimeString([], { timeStyle: 'short' }))
                        .join(', ')}
                    </span>
                  </>
                )}
                .
              </p>
              {plan.note && <p className="mt-2 text-xs text-slate-500">{plan.note}</p>}
            </>
          ) : (
            <p className="text-slate-300">
              Tonight's run would <span className="text-amber-300">skip</span>
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
    <div className="rounded-xl border border-slate-800 bg-slate-950 p-4">
      <div className="mb-3 flex items-center justify-between">
        <p className="text-xs tracking-wide text-slate-500 uppercase">{label}</p>
        <span className="text-slate-500">{icon}</span>
      </div>
      <p className="truncate text-xl font-semibold text-white">{value}</p>
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
      <p className="text-xs tracking-wide text-slate-500 uppercase">{label}</p>
      <p
        className={cn(
          'mt-1 text-2xl font-semibold tabular-nums',
          accent === 'blue' && 'text-sky-400',
          accent === 'green' && 'text-emerald-400',
          !accent && 'text-slate-200 capitalize',
        )}
      >
        {value}
      </p>
    </div>
  )
}
