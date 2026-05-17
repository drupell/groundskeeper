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
        <h1 className="text-3xl font-semibold tracking-tight text-white">Logs</h1>
        <p className="mt-1 text-sm text-slate-400">Most recent executor activity, newest first.</p>
      </header>

      {error && (
        <div className="mb-6 rounded-lg border border-red-900/60 bg-red-950/40 px-4 py-3 text-sm text-red-200">
          {error}
        </div>
      )}

      {loading && logs.length === 0 ? (
        <div className="text-sm text-slate-500">Loading…</div>
      ) : logs.length === 0 ? (
        <div className="rounded-xl border border-slate-800 bg-slate-950 p-10 text-center text-sm text-slate-500">
          No commits yet.
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
                  'rounded-lg border bg-slate-950 px-4 py-3',
                  isOk ? 'border-slate-800' : 'border-amber-900/40 bg-amber-950/10',
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
                    <span className="font-medium text-slate-200">
                      {isOk
                        ? log.type === 'destructive'
                          ? 'Destructive edit'
                          : 'Creative edit'
                        : 'Failure'}
                    </span>
                  </div>
                  {when && (
                    <span className="text-xs text-slate-500 tabular-nums">
                      {new Date(when).toLocaleString()}
                    </span>
                  )}
                </header>
                {isOk ? (
                  <div className="space-y-1 text-sm text-slate-300">
                    {log.path && <div className="font-mono text-xs text-slate-400">{log.path}</div>}
                    {log.message && <div>{log.message}</div>}
                    {log.commit_url && (
                      <a
                        href={log.commit_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-block text-xs text-sky-400 hover:text-sky-300"
                      >
                        View on GitHub →
                      </a>
                    )}
                  </div>
                ) : (
                  <div className="text-sm">
                    <div className="text-amber-300">{log.code ?? 'error'}</div>
                    {log.message && <div className="mt-0.5 text-slate-300">{log.message}</div>}
                  </div>
                )}
              </article>
            )
          })}

          {logs.length >= limit && (
            <button
              type="button"
              onClick={() => setLimit((l) => l + PAGE)}
              className="w-full rounded-lg border border-slate-800 bg-slate-900/50 px-4 py-2 text-sm text-slate-300 transition-colors hover:bg-slate-900"
            >
              Load more
            </button>
          )}
        </div>
      )}
    </div>
  )
}
