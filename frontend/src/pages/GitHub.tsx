// GitHub connection management.
//
// The PAT flow is two-stage: paste → verify (GitHub round-trip, no state
// change) → preview card → save (writes the token to Secrets Manager). The
// repo flow is one-shot: paste a URL → the API Lambda validates with the
// stored PAT and persists the resolved metadata in one call.

import {
  AlertCircle,
  Check,
  ExternalLink,
  GitBranch,
  Loader,
  Lock,
  RefreshCw,
  Trash2,
} from 'lucide-react'
import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { GithubMark } from '@/components/icons'
import { Card, CardFooter, CardHeader } from '@/components/ui/Card'
import { useConfig, useGithubStatus, useRepoInfo } from '@/hooks/useApi'
import type { GitHubStatus } from '@/lib/api'
import { apiClient } from '@/lib/api'
import { cn } from '@/lib/utils'

type Stage =
  | { kind: 'idle' }
  | { kind: 'verifying' }
  | { kind: 'preview'; user: GitHubStatus }
  | { kind: 'saving' }
  | { kind: 'error'; message: string }

export function GitHubPage() {
  return (
    <div className="px-8 py-10">
      <header className="mb-8">
        <h1 className="text-3xl font-semibold tracking-tight text-[var(--color-fg)]">GitHub</h1>
        <p className="mt-1 text-sm text-[var(--color-fg-muted)]">
          Connect your Personal Access Token, then point Groundskeeper at a repo you own.
        </p>
      </header>

      <div className="max-w-2xl space-y-6">
        <PatCard />
        <RepoCard />
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// PAT card — three visual states: not connected / verifying preview / connected
// ---------------------------------------------------------------------------

function PatCard() {
  const { status, loading: statusLoading, refetch } = useGithubStatus()
  const qc = useQueryClient()
  const [token, setToken] = useState('')
  const [stage, setStage] = useState<Stage>({ kind: 'idle' })

  // The repo card's visibility derives from github-status + config, so any
  // PAT change has to invalidate those too — otherwise the UI looks stale
  // until a manual refresh.
  const refreshAll = async () => {
    await Promise.all([
      refetch(),
      qc.invalidateQueries({ queryKey: ['config'] }),
      qc.invalidateQueries({ queryKey: ['repo-info'] }),
    ])
  }

  const verify = async () => {
    const t = token.trim()
    if (!t) {
      setStage({ kind: 'error', message: 'Paste a token first, then we can verify it.' })
      return
    }
    setStage({ kind: 'verifying' })
    try {
      const user = await apiClient.verifyGithubToken(t)
      setStage({ kind: 'preview', user })
    } catch (e) {
      setStage({
        kind: 'error',
        message:
          e instanceof Error
            ? e.message
            : "Couldn't verify that token — double-check it and try again.",
      })
    }
  }

  const save = async () => {
    setStage({ kind: 'saving' })
    try {
      await apiClient.setGithubPat(token.trim())
      setToken('')
      setStage({ kind: 'idle' })
      await refreshAll()
    } catch (e) {
      setStage({
        kind: 'error',
        message: e instanceof Error ? e.message : "Couldn't save the token — give it another go.",
      })
    }
  }

  const cancel = () => {
    setStage({ kind: 'idle' })
  }

  const disconnect = async () => {
    setStage({ kind: 'saving' })
    try {
      await apiClient.clearGithubPat()
      setStage({ kind: 'idle' })
      await refreshAll()
    } catch (e) {
      setStage({
        kind: 'error',
        message: e instanceof Error ? e.message : "Couldn't disconnect — try again in a moment.",
      })
    }
  }

  return (
    <Card>
      <CardHeader title="Personal Access Token" icon={<GithubMark size={18} />}>
        <ConnectionBadge loading={statusLoading} connected={status.connected} />
      </CardHeader>

      {stage.kind === 'error' && (
        <Banner kind="error">
          <AlertCircle size={16} className="mt-0.5 shrink-0" />
          <span>{stage.message}</span>
        </Banner>
      )}

      {stage.kind === 'preview' ? (
        <PreviewBody user={stage.user} onSave={save} onCancel={cancel} />
      ) : status.connected ? (
        <ConnectedBody status={status} onDisconnect={disconnect} onRefresh={refetch} />
      ) : (
        <InputBody
          token={token}
          setToken={setToken}
          onVerify={verify}
          busy={stage.kind === 'verifying' || stage.kind === 'saving'}
        />
      )}

      <CardFooter>
        Don't have one? Generate it at{' '}
        <a
          href="https://github.com/settings/tokens/new"
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-0.5 text-[var(--color-brand)] hover:text-[var(--color-brand-hover)]"
        >
          github.com/settings/tokens/new
          <ExternalLink size={11} />
        </a>{' '}
        and tick the <code className="font-mono text-[var(--color-fg)]">repo</code> scope.
      </CardFooter>
    </Card>
  )
}

function InputBody({
  token,
  setToken,
  onVerify,
  busy,
}: {
  token: string
  setToken: (v: string) => void
  onVerify: () => void
  busy: boolean
}) {
  return (
    <div className="space-y-3">
      <p className="text-sm text-[var(--color-fg-muted)]">
        Paste your token below — we'll check it with GitHub before saving anything.
      </p>
      <input
        type="password"
        value={token}
        onChange={(e) => setToken(e.target.value)}
        placeholder="ghp_…"
        className="w-full rounded-md border border-[var(--color-border)] bg-[var(--color-surface-sunk)] px-3 py-2 text-sm text-[var(--color-fg)] placeholder:text-[var(--color-fg-dim)] focus:border-[var(--color-brand)] focus:ring-1 focus:ring-[var(--color-brand)] focus:outline-none"
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !busy && token.trim()) onVerify()
        }}
      />
      <button
        type="button"
        onClick={onVerify}
        disabled={busy || !token.trim()}
        className="inline-flex w-full items-center justify-center gap-2 rounded-md bg-[var(--color-ink)] px-3 py-2 text-sm font-medium text-[var(--color-ink-fg)] transition-colors hover:bg-[var(--color-ink-hover)] disabled:cursor-not-allowed disabled:bg-[var(--color-border)] disabled:text-[var(--color-fg-dim)]"
      >
        {busy && <Loader size={14} className="animate-spin" />}
        Verify token
      </button>
    </div>
  )
}

function PreviewBody({
  user,
  onSave,
  onCancel,
}: {
  user: GitHubStatus
  onSave: () => void
  onCancel: () => void
}) {
  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-sunk)] p-4">
        <UserRow user={user} />
        <ScopeRow scopes={user.scopes ?? []} sufficient={user.sufficient} />
      </div>

      {user.sufficient === false && (
        <Banner kind="warning">
          <AlertCircle size={16} className="mt-0.5 shrink-0" />
          <span>
            This token's missing the <code className="font-mono">repo</code> scope. Regenerate it
            with that box ticked before saving.
          </span>
        </Banner>
      )}

      <div className="flex gap-2">
        <button
          type="button"
          onClick={onSave}
          disabled={user.sufficient === false}
          className="inline-flex flex-1 items-center justify-center gap-2 rounded-md bg-[var(--color-ink)] px-3 py-2 text-sm font-medium text-[var(--color-ink-fg)] transition-colors hover:bg-[var(--color-ink-hover)] disabled:cursor-not-allowed disabled:bg-[var(--color-border)] disabled:text-[var(--color-fg-dim)]"
        >
          Save token
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="rounded-md border border-[var(--color-border)] px-3 py-2 text-sm text-[var(--color-fg)] transition-colors hover:bg-[var(--color-surface-sunk)]"
        >
          Cancel
        </button>
      </div>
    </div>
  )
}

function ConnectedBody({
  status,
  onDisconnect,
  onRefresh,
}: {
  status: GitHubStatus
  onDisconnect: () => void
  onRefresh: () => void
}) {
  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-sunk)] p-4">
        <UserRow user={status} />
        <ScopeRow scopes={status.scopes ?? []} sufficient={status.sufficient} />
      </div>

      <div className="flex items-center justify-between gap-2">
        <button
          type="button"
          onClick={onRefresh}
          className="inline-flex items-center gap-1.5 text-xs text-[var(--color-fg-muted)] transition-colors hover:text-[var(--color-fg)]"
        >
          <RefreshCw size={12} /> Refresh
        </button>
        <button
          type="button"
          onClick={onDisconnect}
          className="inline-flex items-center gap-1.5 rounded-md border border-[var(--color-border)] px-3 py-1.5 text-xs text-[var(--color-fg)] transition-colors hover:border-rose-900/60 hover:bg-rose-950/30 hover:text-rose-200"
        >
          <Trash2 size={12} /> Disconnect
        </button>
      </div>
    </div>
  )
}

function UserRow({ user }: { user: GitHubStatus }) {
  return (
    <div className="flex items-center gap-4">
      {user.avatar_url ? (
        <img
          src={user.avatar_url}
          alt=""
          className="h-12 w-12 rounded-full border border-[var(--color-border)] bg-[var(--color-surface-elevated)]"
        />
      ) : (
        <div className="flex h-12 w-12 items-center justify-center rounded-full border border-[var(--color-border)] bg-[var(--color-surface-elevated)] text-[var(--color-fg-muted)]">
          <GithubMark size={20} />
        </div>
      )}
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium text-[var(--color-fg)]">
          {user.name ?? user.login ?? 'Name unavailable'}
        </p>
        {user.login && user.name && user.login !== user.name && (
          <p className="truncate text-xs text-[var(--color-fg-muted)]">@{user.login}</p>
        )}
        {user.email && (
          <p className="truncate text-xs text-[var(--color-fg-muted)]">{user.email}</p>
        )}
      </div>
    </div>
  )
}

function ScopeRow({ scopes, sufficient }: { scopes: string[]; sufficient?: boolean }) {
  const hasRepo = scopes.includes('repo') || scopes.some((s) => s.startsWith('repo:'))
  return (
    <div className="mt-3 flex flex-wrap items-center gap-1.5 border-t border-[var(--color-border)] pt-3">
      <span className="text-xs tracking-wide text-[var(--color-fg-muted)] uppercase">Scopes</span>
      {scopes.length === 0 ? (
        <span className="text-xs text-[var(--color-fg-dim)]">none reported</span>
      ) : (
        scopes.map((scope) => {
          const isRepo = scope === 'repo' || scope.startsWith('repo:')
          return (
            <span
              key={scope}
              className={cn(
                'inline-flex items-center gap-1 rounded-full border px-2 py-0.5 font-mono text-[11px]',
                isRepo
                  ? 'border-emerald-900/40 bg-emerald-950/30 text-emerald-300'
                  : 'border-[var(--color-border)] bg-[var(--color-surface-elevated)] text-[var(--color-fg-muted)]',
              )}
            >
              {isRepo && <Check size={10} />} {scope}
            </span>
          )
        })
      )}
      {sufficient !== undefined && !hasRepo && (
        <span className="ml-1 inline-flex items-center gap-1 text-[11px] text-amber-300">
          <AlertCircle size={11} /> needs <code className="font-mono">repo</code>
        </span>
      )}
    </div>
  )
}

function ConnectionBadge({ loading, connected }: { loading: boolean; connected: boolean }) {
  if (loading) {
    return (
      <span className="inline-flex items-center gap-1.5 text-xs text-[var(--color-fg-muted)]">
        <Loader size={12} className="animate-spin" /> Checking with GitHub…
      </span>
    )
  }
  if (connected) {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full border border-emerald-900/40 bg-emerald-950/30 px-2 py-0.5 text-xs text-emerald-300">
        <Check size={12} /> Connected
      </span>
    )
  }
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-[var(--color-border)] bg-[var(--color-surface-elevated)] px-2 py-0.5 text-xs text-[var(--color-fg-muted)]">
      Not connected
    </span>
  )
}

// ---------------------------------------------------------------------------
// Repo card — disabled until PAT is connected; shows live info once a repo
// is wired up
// ---------------------------------------------------------------------------

function RepoCard() {
  const { status: githubStatus } = useGithubStatus()
  const { config } = useConfig()
  const qc = useQueryClient()
  const isConnected = githubStatus.connected
  const hasRepo = Boolean(config?.repo?.full_name)
  const {
    repo,
    loading: repoLoading,
    error: repoError,
    refetch: refetchRepo,
  } = useRepoInfo(isConnected && hasRepo)

  const [editing, setEditing] = useState(false)
  const [repoUrl, setRepoUrl] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const connect = async () => {
    const u = repoUrl.trim()
    if (!u) {
      setErr("Paste a repo URL — or just owner/repo if that's easier.")
      return
    }
    setBusy(true)
    setErr(null)
    try {
      await apiClient.setRepoUrl(u)
      setRepoUrl('')
      setEditing(false)
      // hasRepo derives from ['config']; invalidate it (and the live repo
      // query) so the card flips to the display view immediately.
      await Promise.all([
        qc.invalidateQueries({ queryKey: ['config'] }),
        qc.invalidateQueries({ queryKey: ['repo-info'] }),
        refetchRepo(),
      ])
    } catch (e) {
      setErr(
        e instanceof Error ? e.message : "Couldn't connect that repo. Check the URL and try again.",
      )
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card>
      <CardHeader title="Repository" icon={<GitBranch size={18} />}>
        {hasRepo && repo?.private && (
          <span className="inline-flex items-center gap-1 rounded-full border border-[var(--color-border)] bg-[var(--color-surface-elevated)] px-2 py-0.5 text-xs text-[var(--color-fg-muted)]">
            <Lock size={11} /> private
          </span>
        )}
      </CardHeader>

      {!isConnected ? (
        <p className="text-sm text-[var(--color-fg-muted)]">
          Connect your GitHub token above first.
        </p>
      ) : hasRepo && !editing ? (
        <RepoDisplay
          repo={repo}
          loading={repoLoading}
          error={repoError}
          savedFullName={config?.repo?.full_name}
          onChange={() => {
            setEditing(true)
            setRepoUrl(config?.repo?.full_name ?? '')
          }}
        />
      ) : (
        <RepoEditor
          repoUrl={repoUrl}
          setRepoUrl={setRepoUrl}
          onConnect={connect}
          onCancel={hasRepo ? () => setEditing(false) : undefined}
          busy={busy}
          err={err}
        />
      )}

      <CardFooter>
        Your token needs push access. Pick a personal repo you don't mind looking a little
        automated.
      </CardFooter>
    </Card>
  )
}

function RepoDisplay({
  repo,
  loading,
  error,
  savedFullName,
  onChange,
}: {
  repo: {
    full_name: string
    html_url?: string
    description?: string | null
    default_branch: string
    permissions?: { push?: boolean }
    last_commit?: {
      sha: string
      message: string
      author: string
      date: string
      html_url?: string
    } | null
  } | null
  loading: boolean
  error: string | null
  savedFullName?: string
  onChange: () => void
}) {
  // While the live fetch is in flight, fall back to the persisted name.
  const fullName = repo?.full_name ?? savedFullName ?? 'unknown repo'
  const canPush = repo?.permissions?.push !== false

  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-sunk)] p-4">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <p className="truncate font-mono text-sm text-[var(--color-fg)]">{fullName}</p>
            {repo?.description && (
              <p className="mt-1 text-sm text-[var(--color-fg-muted)]">{repo.description}</p>
            )}
            {!repo && loading && (
              <p className="mt-1 text-xs text-[var(--color-fg-dim)]">Fetching repo…</p>
            )}
            {!repo && error && (
              <p className="mt-1 text-xs text-[var(--color-brand)]">
                Couldn't reach GitHub: {error}
              </p>
            )}
          </div>
          {repo?.html_url && (
            <a
              href={repo.html_url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-xs text-[var(--color-brand)] hover:text-[var(--color-brand-hover)]"
            >
              Open <ExternalLink size={11} />
            </a>
          )}
        </div>

        {repo && (
          <div className="mt-4 flex flex-wrap gap-2 border-t border-[var(--color-border)] pt-3 text-xs">
            <Pill icon={<GitBranch size={11} />} label={`branch: ${repo.default_branch}`} />
            {!canPush && (
              <Pill
                tone="warn"
                icon={<AlertCircle size={11} />}
                label="Token can't push to this repo"
              />
            )}
          </div>
        )}

        {repo?.last_commit && (
          <div className="mt-3 border-t border-[var(--color-border)] pt-3">
            <p className="text-xs tracking-wide text-[var(--color-fg-muted)] uppercase">
              Last commit
            </p>
            <p className="mt-1 text-sm text-[var(--color-fg)]">{repo.last_commit.message}</p>
            <p className="mt-0.5 text-xs text-[var(--color-fg-muted)]">
              <span className="font-mono">{repo.last_commit.sha.slice(0, 7)}</span> · by{' '}
              {repo.last_commit.author} ·{' '}
              <time dateTime={repo.last_commit.date}>
                {new Date(repo.last_commit.date).toLocaleString()}
              </time>
            </p>
          </div>
        )}
      </div>

      <div className="flex justify-end">
        <button
          type="button"
          onClick={onChange}
          className="rounded-md border border-[var(--color-border)] px-3 py-1.5 text-xs text-[var(--color-fg)] transition-colors hover:bg-[var(--color-surface-sunk)]"
        >
          Change repository
        </button>
      </div>
    </div>
  )
}

function RepoEditor({
  repoUrl,
  setRepoUrl,
  onConnect,
  onCancel,
  busy,
  err,
}: {
  repoUrl: string
  setRepoUrl: (v: string) => void
  onConnect: () => void
  onCancel?: () => void
  busy: boolean
  err: string | null
}) {
  return (
    <div className="space-y-3">
      {err && (
        <Banner kind="error">
          <AlertCircle size={16} className="mt-0.5 shrink-0" />
          <span>{err}</span>
        </Banner>
      )}
      <input
        type="text"
        value={repoUrl}
        onChange={(e) => setRepoUrl(e.target.value)}
        placeholder="https://github.com/you/your-repo  ·  or  owner/repo"
        className="w-full rounded-md border border-[var(--color-border)] bg-[var(--color-surface-sunk)] px-3 py-2 text-sm text-[var(--color-fg)] placeholder:text-[var(--color-fg-dim)] focus:border-[var(--color-brand)] focus:ring-1 focus:ring-[var(--color-brand)] focus:outline-none"
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !busy && repoUrl.trim()) onConnect()
        }}
      />
      <div className="flex gap-2">
        <button
          type="button"
          onClick={onConnect}
          disabled={busy || !repoUrl.trim()}
          className="inline-flex flex-1 items-center justify-center gap-2 rounded-md bg-[var(--color-ink)] px-3 py-2 text-sm font-medium text-[var(--color-ink-fg)] transition-colors hover:bg-[var(--color-ink-hover)] disabled:cursor-not-allowed disabled:bg-[var(--color-border)] disabled:text-[var(--color-fg-dim)]"
        >
          {busy && <Loader size={14} className="animate-spin" />}
          Validate & save
        </button>
        {onCancel && (
          <button
            type="button"
            onClick={onCancel}
            className="rounded-md border border-[var(--color-border)] px-3 py-2 text-sm text-[var(--color-fg)] transition-colors hover:bg-[var(--color-surface-sunk)]"
          >
            Cancel
          </button>
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Small shared chrome
// ---------------------------------------------------------------------------

function Banner({
  kind,
  children,
}: {
  kind: 'error' | 'warning' | 'success'
  children: React.ReactNode
}) {
  return (
    <div
      className={cn(
        'mb-4 flex items-start gap-2 rounded-md border px-3 py-2 text-sm',
        kind === 'error' && 'border-rose-900/40 bg-rose-950/20 text-rose-200',
        kind === 'warning' && 'border-amber-900/40 bg-amber-950/20 text-amber-200',
        kind === 'success' && 'border-emerald-900/40 bg-emerald-950/20 text-emerald-200',
      )}
    >
      {children}
    </div>
  )
}

function Pill({
  icon,
  label,
  tone = 'neutral',
}: {
  icon?: React.ReactNode
  label: string
  tone?: 'neutral' | 'warn'
}) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-full border px-2 py-0.5',
        tone === 'warn'
          ? 'border-amber-900/40 bg-amber-950/20 text-amber-200'
          : 'border-[var(--color-border)] bg-[var(--color-surface-elevated)] text-[var(--color-fg-muted)]',
      )}
    >
      {icon}
      {label}
    </span>
  )
}
