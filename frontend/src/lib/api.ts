// API client. Singleton instantiated from a build-time env var written by
// deploy.py into frontend/.env.production. The whole dashboard sits behind
// Amplify basic auth, which is the single gate on every endpoint — no
// per-request API key on top.

export interface ApiError {
  error: string
  message: string
}

export interface DayConfig {
  enabled: boolean
  start: string
  end: string
}

export interface CommitCountConfig {
  min: number
  max: number
  /** [x_norm, y] control points where x_norm ∈ [0, 1] spans [min, max]. */
  curve: number[][]
}

export interface CommitStyleConfig {
  prompt: string
  destructive_probability: number
  min_lines: number
  max_lines: number
}

export interface RepoConfig {
  url: string
  owner: string
  name: string
  full_name: string
  default_branch: string
  description?: string | null
  private: boolean
  /** GitHub's stable numeric repo id. Used to detect renames + auto-heal. */
  id?: number
}

export interface Config {
  version: number
  timezone: string
  vacation: { active: boolean; until?: string | null }
  gap: { min_minutes: number; max_minutes: number }
  commit_count: CommitCountConfig
  schedule: Record<string, DayConfig>
  commit_style: CommitStyleConfig
  repo: RepoConfig | null
}

export interface GitHubStatus {
  connected: boolean
  login?: string
  name?: string
  email?: string | null
  avatar_url?: string
  html_url?: string
  scopes?: string[]
  sufficient?: boolean
}

export interface RepoInfo extends RepoConfig {
  html_url?: string
  permissions?: { admin?: boolean; push?: boolean; pull?: boolean }
  last_commit?: {
    sha: string
    message: string
    author: string
    date: string
    html_url?: string
  } | null
}

export interface RunRecord {
  local_date: string
  timezone: string
  count_sampled: number
  count_placed: number
  commit_times_utc: string[]
  schedule_names: string[]
  status: string
  created_at: string
}

export interface LogEntry {
  status: 'ok' | 'failed'
  type?: string
  path?: string
  sha?: string
  commit_url?: string
  message?: string
  scheduled_at?: string
  run_date?: string
  schedule_name?: string
  committed_at?: string
  logged_at?: string
  code?: string
}

export interface DashboardStatus {
  vacation: { active: boolean; until?: string | null }
  timezone: string
  last_run: RunRecord | null
  recent_logs: LogEntry[]
}

export interface DryRunResult {
  status: string
  reason?: string
  target_date?: string
  timezone?: string
  window?: { start: string; end: string }
  count_sampled?: number
  planned_times_local?: string[]
  note?: string
}

export type ApiClientErrorKind = 'network' | 'http' | 'parse'

export class ApiClientError extends Error {
  readonly kind: ApiClientErrorKind
  readonly status?: number

  constructor(kind: ApiClientErrorKind, message: string, status?: number) {
    super(message)
    this.name = 'ApiClientError'
    this.kind = kind
    this.status = status
  }
}

class ApiClient {
  constructor(private readonly baseUrl: string) {}

  private async request<T>(method: string, path: string, body?: unknown): Promise<T> {
    const headers: Record<string, string> = { Accept: 'application/json' }
    if (body !== undefined) headers['Content-Type'] = 'application/json'

    let res: Response
    try {
      res = await fetch(`${this.baseUrl}${path}`, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
      })
    } catch (e) {
      // fetch only throws on network failure (DNS, offline, CORS preflight, etc.)
      throw new ApiClientError(
        'network',
        e instanceof Error && e.message ? e.message : 'Network request failed',
      )
    }

    if (res.status === 204) return undefined as T

    const raw = await res.text()
    let parsed: unknown
    let parseFailed = false
    if (raw) {
      try {
        parsed = JSON.parse(raw)
      } catch {
        parsed = { message: raw || res.statusText }
        parseFailed = true
      }
    } else {
      parsed = null
    }

    if (!res.ok) {
      const err = parsed as Partial<ApiError> | null
      throw new ApiClientError(
        'http',
        err?.message || `Couldn't reach the API (${res.status}) — try again in a moment.`,
        res.status,
      )
    }
    if (parseFailed) {
      throw new ApiClientError('parse', 'The API returned a response we couldn’t read.')
    }
    return parsed as T
  }

  // System
  health = () => this.request<{ status: string }>('GET', '/health')

  // Config
  getConfig = () => this.request<Config>('GET', '/config')
  putConfig = (c: Config) => this.request<Config>('PUT', '/config', c)
  patchConfig = (patch: Partial<Config>) => this.request<Config>('PATCH', '/config', patch)

  // GitHub
  verifyGithubToken = (token: string) =>
    this.request<GitHubStatus>('POST', '/github/verify', { token })
  setGithubPat = (token: string) =>
    this.request<GitHubStatus & { connected: true }>('PUT', '/github/pat', { token })
  clearGithubPat = () => this.request<{ connected: false }>('DELETE', '/github/pat')
  getGithubStatus = () => this.request<GitHubStatus>('GET', '/github/status')
  getRepoInfo = () => this.request<RepoInfo>('GET', '/github/repo')
  setRepoUrl = (url: string) => this.request<RepoInfo>('PUT', '/github/repo', { url })

  // History
  getLogs = (limit = 50) => this.request<{ items: LogEntry[] }>('GET', `/logs?limit=${limit}`)
  getRuns = (limit = 14) => this.request<{ items: RunRecord[] }>('GET', `/runs?limit=${limit}`)
  getDashboardStatus = () => this.request<DashboardStatus>('GET', '/status')

  // Operations
  setVacation = (active: boolean, until?: string | null) =>
    this.request<Config>('PUT', '/vacation', { active, until: until ?? null })
  rotatePassword = (newPassword: string) =>
    this.request<{ rotated: boolean }>('POST', '/password', { new: newPassword })

  // Testing
  testRun = () => this.request<{ status: string; message: string }>('POST', '/test/run')
  testPlan = () => this.request<DryRunResult>('POST', '/test/plan')

  // Manual orchestrator run (not dry-run — creates real schedules + RUN#)
  runNow = () => this.request<{ status: string; message: string }>('POST', '/run/now')
}

const baseUrl = (import.meta.env.VITE_API_URL as string | undefined) ?? ''

export const apiClient = new ApiClient(baseUrl)

/** Kept for backwards-compat with any callers still importing `useApi`. */
export function useApi() {
  return apiClient
}
