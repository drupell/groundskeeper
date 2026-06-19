import { useState } from 'react'

import { useLogs } from '@/hooks/useApi'
import { cn } from '@/lib/utils'

const PAGE = 50

export function LogsPage() {
  const [limit, setLimit] = useState(PAGE)
  const { logs, loading, error } = useLogs(limit)

  return (
    <div className="px-8 py-10">
      <header className="mb-8">
        <h1 className="text-3xl font-semibold tracking-tight text-[var(--color-fg)]">Logs</h1>
        <p className="mt-1 text-sm text-[var(--color-fg-muted)]">
          A running record of recent commits — newest first.
        </p>
      </header>

      {error && (
        <div className="mb-6 rounded-lg border border-rose-900/60 bg-rose-950/40 px-4 py-3 text-sm text-rose-200">
          {error}
        </div>
      )}

      {loading && logs.length === 0 ? (
        <div className="text-sm text-[var(--color-fg-muted)]">One moment…</div>
      ) : logs.length === 0 ? (
        <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface-elevated)] p-10 text-center text-sm text-[var(--color-fg-muted)]">
          Nothing here yet. Once we make a commit, it'll show up on this page.
        </div>
      ) : (
        <div className="space-y-3">
          {logs.map((log, idx) => {
            const isOk = log.status === 'ok'
            const when = log.committed_at ?? log.logged_at
            return (
              <article
                key={idx}
                className={cn(
                  'rounded-lg border bg-[var(--color-surface-elevated)] px-4 py-3',
                  isOk ? 'border-[var(--color-border)]' : 'border-amber-900/40 bg-amber-950/10',
                )}
              >
                <header className="mb-1.5 flex items-center justify-between gap-3">
                  <div className="flex items-center gap-2 text-sm">
                    <span
                      className={cn(
                        'inline-block h-2 w-2 rounded-full',
                        isOk ? 'bg-emerald-400' : 'bg-amber-400',
                      )}
                    />
                    <span className="font-medium text-[var(--color-fg)]">
                      {isOk
                        ? log.type === 'destructive'
                          ? 'Maintenance edit'
                          : 'Creative edit'
                        : 'Something went wrong'}
                    </span>
                  </div>
                  {when && (
                    <span className="text-xs text-[var(--color-fg-muted)] tabular-nums">
                      {new Date(when).toLocaleString()}
                    </span>
                  )}
                </header>
                {isOk ? (
                  <div className="space-y-1 text-sm text-[var(--color-fg)]">
                    {log.path && (
                      <div className="font-mono text-xs text-[var(--color-fg-muted)]">
                        {log.path}
                      </div>
                    )}
                    {log.message && <div>{log.message}</div>}
                    {log.commit_url && (
                      <a
                        href={log.commit_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-block text-xs text-[var(--color-brand)] hover:text-[var(--color-brand-hover)]"
                      >
                        View on GitHub →
                      </a>
                    )}
                  </div>
                ) : (
                  <div className="text-sm">
                    <div className="text-amber-300">{log.code ?? 'Unknown error'}</div>
                    {log.message && (
                      <div className="mt-0.5 text-[var(--color-fg)]">{log.message}</div>
                    )}
                  </div>
                )}
              </article>
            )
          })}

          {logs.length >= limit && (
            <button
              type="button"
              onClick={() => setLimit((l) => l + PAGE)}
              className="w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-sunk)] px-4 py-2 text-sm text-[var(--color-fg)] transition-colors hover:bg-[var(--color-surface-elevated)]"
            >
              Show more
            </button>
          )}
        </div>
      )}
    </div>
  )
}
