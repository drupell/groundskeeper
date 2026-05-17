// Thin React Query wrappers around the API client. The return shapes are
// preserved as `{ data: …, loading, error, action }` rather than React Query's
// raw query objects so existing pages don't need to change.

import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import type {
  Config,
  DashboardStatus,
  DryRunResult,
  GitHubStatus,
  RepoInfo,
  RunRecord,
} from '@/lib/api'
import { apiClient } from '@/lib/api'

const errMsg = (error: unknown): string | null =>
  error ? (error instanceof Error ? error.message : String(error)) : null

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
  const run = useMutation({
    mutationFn: () => apiClient.testRun(),
    onSuccess: () => {
      // The executor was invoked async; give it a beat, then pull the logs
      // it writes so the result appears without a manual refresh.
      qc.invalidateQueries({ queryKey: ['dashboard-status'] })
      window.setTimeout(() => {
        qc.invalidateQueries({ queryKey: ['logs'] })
        qc.invalidateQueries({ queryKey: ['dashboard-status'] })
      }, 4_000)
      window.setTimeout(() => {
        qc.invalidateQueries({ queryKey: ['logs'] })
        qc.invalidateQueries({ queryKey: ['dashboard-status'] })
      }, 9_000)
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
