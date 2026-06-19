// Thin React Query wrappers around the API client. The return shapes are
// preserved as `{ data: …, loading, error, action }` rather than React Query's
// raw query objects so existing pages don't need to change.

import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef } from 'react'

import type {
  Config,
  DashboardStatus,
  DryRunResult,
  GitHubStatus,
  RepoInfo,
  RunRecord,
} from '@/lib/api'
import { ApiClientError, apiClient } from '@/lib/api'

const errMsg = (error: unknown): string | null => {
  if (!error) return null
  if (error instanceof ApiClientError) {
    if (error.kind === 'network') {
      return "Can't reach the API — looks like you're offline. Try again in a moment."
    }
    if (error.kind === 'parse') {
      return "The API responded but we couldn't read it. Try again in a moment."
    }
    return error.message
  }
  return error instanceof Error ? error.message : String(error)
}

// ---------------------------------------------------------------------------
// Config — GET + PATCH
// ---------------------------------------------------------------------------
export function useConfig() {
  const qc = useQueryClient()
  const query = useQuery({
    queryKey: ['config'],
    queryFn: () => apiClient.getConfig(),
  })
  const mutation = useMutation({
    mutationFn: (patch: Partial<Config>) => apiClient.patchConfig(patch),
    onSuccess: (data) => qc.setQueryData(['config'], data),
  })
  return {
    config: query.data ?? null,
    loading: query.isLoading,
    error: errMsg(query.error),
    updateConfig: (patch: Partial<Config>) => mutation.mutateAsync(patch),
    saving: mutation.isPending,
  }
}

// ---------------------------------------------------------------------------
// GitHub status — what's stored locally + whether a PAT is set
// ---------------------------------------------------------------------------
export function useGithubStatus() {
  const query = useQuery({
    queryKey: ['github-status'],
    queryFn: () => apiClient.getGithubStatus(),
  })
  const fallback: GitHubStatus = { connected: false }
  return {
    status: query.data ?? fallback,
    loading: query.isLoading,
    error: errMsg(query.error),
    refetch: query.refetch,
  }
}

// ---------------------------------------------------------------------------
// Composite dashboard payload (vacation, last run, recent logs)
// ---------------------------------------------------------------------------
export function useDashboardStatus() {
  const query = useQuery({
    queryKey: ['dashboard-status'],
    queryFn: () => apiClient.getDashboardStatus(),
    refetchInterval: 60_000,
  })
  return {
    status: (query.data ?? null) as DashboardStatus | null,
    loading: query.isLoading,
    error: errMsg(query.error),
  }
}

// ---------------------------------------------------------------------------
// Logs page — paginated commit log entries
// ---------------------------------------------------------------------------
export function useLogs(limit: number) {
  const query = useQuery({
    queryKey: ['logs', limit],
    queryFn: () => apiClient.getLogs(limit),
    placeholderData: keepPreviousData,
  })
  return {
    logs: query.data?.items ?? [],
    loading: query.isLoading,
    error: errMsg(query.error),
    refetch: query.refetch,
    fetching: query.isFetching,
  }
}

// ---------------------------------------------------------------------------
// Runs (used by the Schedule page later)
// ---------------------------------------------------------------------------
export function useRuns(limit: number) {
  const query = useQuery({
    queryKey: ['runs', limit],
    queryFn: () => apiClient.getRuns(limit),
  })
  return {
    runs: (query.data?.items ?? []) as RunRecord[],
    loading: query.isLoading,
    error: errMsg(query.error),
    refetch: query.refetch,
    fetching: query.isFetching,
  }
}

// ---------------------------------------------------------------------------
// Vacation toggle — PUT /vacation, then refresh anything that shows it
// ---------------------------------------------------------------------------
export function useVacation() {
  const qc = useQueryClient()
  const mutation = useMutation({
    mutationFn: ({ active, until }: { active: boolean; until?: string | null }) =>
      apiClient.setVacation(active, until ?? null),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['dashboard-status'] })
      qc.invalidateQueries({ queryKey: ['config'] })
    },
  })
  return {
    setVacation: (active: boolean, until?: string | null) =>
      mutation.mutateAsync({ active, until }),
    saving: mutation.isPending,
    error: errMsg(mutation.error),
  }
}

// ---------------------------------------------------------------------------
// Test tools — fire a real commit now, or preview tonight's plan (dry-run)
// ---------------------------------------------------------------------------
export function useTestRun() {
  const qc = useQueryClient()
  const timers = useRef<number[]>([])
  useEffect(
    () => () => {
      timers.current.forEach(window.clearTimeout)
      timers.current = []
    },
    [],
  )
  const schedule = (ms: number, fn: () => void) => {
    timers.current.push(window.setTimeout(fn, ms))
  }
  const run = useMutation({
    mutationFn: () => apiClient.testRun(),
    onSuccess: () => {
      // The executor was invoked async; give it a beat, then pull the logs
      // it writes so the result appears without a manual refresh.
      qc.invalidateQueries({ queryKey: ['dashboard-status'] })
      schedule(4_000, () => {
        qc.invalidateQueries({ queryKey: ['logs'] })
        qc.invalidateQueries({ queryKey: ['dashboard-status'] })
      })
      schedule(9_000, () => {
        qc.invalidateQueries({ queryKey: ['logs'] })
        qc.invalidateQueries({ queryKey: ['dashboard-status'] })
      })
    },
  })
  const plan = useMutation({ mutationFn: () => apiClient.testPlan() })
  return {
    runTest: () => run.mutateAsync(),
    running: run.isPending,
    runResult: run.data ?? null,
    runError: errMsg(run.error),
    previewPlan: () => plan.mutateAsync(),
    planning: plan.isPending,
    plan: (plan.data ?? null) as DryRunResult | null,
    planError: errMsg(plan.error),
  }
}

// ---------------------------------------------------------------------------
// Run the orchestrator for real, right now — creates schedules + a RUN#.
// Async on the backend, so the call returns immediately; we re-pull the
// runs/logs/status queries on a short delay to surface the result.
// ---------------------------------------------------------------------------
export function useRunNow() {
  const qc = useQueryClient()
  const timers = useRef<number[]>([])
  useEffect(
    () => () => {
      timers.current.forEach(window.clearTimeout)
      timers.current = []
    },
    [],
  )
  const schedule = (ms: number, fn: () => void) => {
    timers.current.push(window.setTimeout(fn, ms))
  }
  const mutation = useMutation({
    mutationFn: () => apiClient.runNow(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['dashboard-status'] })
      schedule(4_000, () => {
        qc.invalidateQueries({ queryKey: ['runs'] })
        qc.invalidateQueries({ queryKey: ['logs'] })
        qc.invalidateQueries({ queryKey: ['dashboard-status'] })
      })
      schedule(10_000, () => {
        qc.invalidateQueries({ queryKey: ['runs'] })
        qc.invalidateQueries({ queryKey: ['logs'] })
      })
    },
  })
  return {
    runScheduler: () => mutation.mutateAsync(),
    running: mutation.isPending,
    result: mutation.data ?? null,
    error: errMsg(mutation.error),
  }
}

// ---------------------------------------------------------------------------
// Repo info — fetched live from GitHub through the API Lambda. Pass
// `enabled=false` when there's no PAT or no repo configured yet, otherwise
// the endpoint returns a 400 we don't care about.
// ---------------------------------------------------------------------------
export function useRepoInfo(enabled = true) {
  const query = useQuery({
    queryKey: ['repo-info'],
    queryFn: () => apiClient.getRepoInfo(),
    retry: false,
    enabled,
  })
  return {
    repo: (query.data ?? null) as RepoInfo | null,
    loading: query.isLoading,
    error: errMsg(query.error),
    refetch: query.refetch,
  }
}
