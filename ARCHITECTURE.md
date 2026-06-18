# Architecture

Groundskeeper is a single-tenant AWS app that automates realistic-looking
GitHub commits on a repo you own. The backend is three Python Lambdas plus
DynamoDB, Secrets Manager, EventBridge Scheduler, and Bedrock Nova Lite.
The frontend is a Vite + React + Tailwind dashboard hosted on Amplify.

## Data flow

```
                       ┌──────────────────────────┐
                       │  EventBridge cron        │
                       │  daily @ 12:00 UTC       │
                       └─────────────┬────────────┘
                                     │ invoke
                                     ▼
                       ┌──────────────────────────┐
   reads CONFIG ◄──────┤    Orchestrator Lambda   │──► writes RUN#<date>
                       │  plans N commit times    │
                       └─────────────┬────────────┘
                                     │ create one-time rules
                                     ▼
                       ┌──────────────────────────┐
                       │  EventBridge Scheduler   │
                       │  group: per-day rules    │  ActionAfterCompletion=DELETE
                       └─────────────┬────────────┘
                                     │ fires per scheduled commit
                                     ▼
                       ┌──────────────────────────┐
   reads GITHUB ◄──────┤     Executor Lambda      │──► writes LOG#<iso>
   reads PAT secret    │  pick file, ask Bedrock  │
                       └─────────────┬────────────┘
                                     │ PUT /repos/:o/:r/contents/:p
                                     ▼
                       ┌──────────────────────────┐
                       │     GitHub Contents API  │
                       └──────────────────────────┘


   ┌─────────────┐   HTTPS   ┌──────────────┐    ┌─────────────┐
   │  Dashboard  │──────────►│ API Gateway  │───►│  API Lambda │
   │  (Amplify)  │◄──────────│  (REST)      │◄───│             │
   └─────────────┘           └──────────────┘    └──────┬──────┘
                                                        │
                       ┌────────────────────────────────┼─────────────────────┐
                       ▼                                ▼                     ▼
              ┌────────────────┐               ┌────────────────┐    ┌────────────────┐
              │  DynamoDB      │               │  Secrets Mgr   │    │  Lambda invoke │
              │  single-table  │               │  (GitHub PAT)  │    │  (run-now)     │
              └────────────────┘               └────────────────┘    └────────────────┘
```

## Components

### Orchestrator Lambda
`backend/lambdas/orchestrator/handler.py`. Triggered by the daily
EventBridge cron. Reads `CONFIG` (schedule, distribution, vacation) and
`GITHUB` (repo + branch). Picks a commit count from the distribution,
spreads commit times across the active window via `shared/timeplan.py`,
creates one-time EventBridge Scheduler rules that invoke the executor,
and writes a `RUN#<YYYY-MM-DD>` record summarizing the plan. Skips
gracefully on vacation days.

### Executor Lambda
`backend/lambdas/executor/handler.py`. Invoked by a Scheduler rule
(payload carries run context). Resolves the current repo via
`shared/github.py:resolve_current`, picks a target file with
`shared/files.py`, asks Bedrock Nova Lite for a small realistic edit
plus a commit message (`shared/bedrock.py`), and PUTs the new contents
through the GitHub Contents API with the no-reply author email so the
commit lights up the contribution graph. Each run appends a
`LOG#<iso-ts>#<rand>` row.

### API Lambda
`backend/lambdas/api/handler.py`. Dispatch table at `_ROUTES` maps
`(method, path)` to handlers. Backs the dashboard: GET/PUT/PATCH
`/config`, GitHub PAT + repo CRUD, `/logs`, `/runs`, `/status`,
`/vacation`, `/password` (Amplify basic-auth rotation), `/test/plan`,
`/test/run`, and `/run/now` (synchronous orchestrator invoke).

### DynamoDB single-table
`shared/ddb.py`. One table, partition key `pk="USER#default"`. Sort keys:

- `CONFIG` — schedule, distribution, commit style, vacation.
- `GITHUB` — verified GitHub user + connected repo metadata.
- `RUN#<YYYY-MM-DD>` — orchestrator run record.
- `LOG#<iso-ts>#<rand>` — per-commit executor log entry.

### Secrets Manager
One secret, `groundskeeper/github-pat`, holding the personal access
token. Accessed via `shared/secrets.py`. Nothing else lives here.

### EventBridge Scheduler
One scheduler group. The orchestrator creates one-time rules with
`ActionAfterCompletion=DELETE` so cleanup is automatic — the group never
accumulates stale entries. Wrapped by `shared/scheduler.py`.

### Bedrock Nova Lite
Model id `amazon.nova-lite-v1:0`. Prompt templates live in
`shared/bedrock.py` (file-edit prompt + commit-message prompt). The
executor is the only caller; IAM scopes the model ARN to this id.

### Amplify
Static hosting for the built `frontend/dist` bundle plus basic-auth on
the app branch. Provisioning + password rotation in `shared/amplify.py`.

### frontend/
Vite + React 18 + TypeScript + Tailwind v4. React Query owns server
state (`src/hooks/useApi.ts` wraps the typed client in `src/lib/api.ts`).
Theme tokens are CSS variables on `<html>`; dark mode is the `.dark`
class toggled by `src/lib/theme.tsx`. Pages: Dashboard, Schedule,
CommitStyle, GitHub, Logs, Settings.

## Repo layout

```
Groundskeeper/
├── deploy.py                  # self-bootstrapping deploy (uv sync + venv)
├── teardown.py                # tag-scoped resource sweeper
├── pyproject.toml
├── backend/
│   ├── shared/
│   │   ├── aws.py             # boto3 client cache
│   │   ├── config.py          # CONFIG defaults + validation
│   │   ├── ddb.py             # single-table helpers, SK constants
│   │   ├── secrets.py         # PAT get/put
│   │   ├── github.py          # urllib client, resolve_current, put_file
│   │   ├── bedrock.py         # Nova Lite prompts + invoke
│   │   ├── files.py           # target-file picker
│   │   ├── timeplan.py        # spread commit times across a window
│   │   ├── distribution.py    # commits-per-day distribution
│   │   ├── scheduler.py       # EventBridge Scheduler wrapper
│   │   ├── amplify.py         # Amplify app + basic-auth
│   │   ├── responses.py       # API Gateway response shapes
│   │   ├── errors.py          # typed exceptions
│   │   └── log.py             # structured JSON logger
│   ├── lambdas/
│   │   ├── orchestrator/handler.py
│   │   ├── executor/handler.py
│   │   └── api/handler.py
│   └── tests/                 # pytest, 61 passing
└── frontend/
    ├── index.html
    ├── vite.config.ts
    ├── tailwind.config.cjs
    └── src/
        ├── App.tsx
        ├── main.tsx
        ├── index.css          # Tailwind + CSS-var design tokens
        ├── pages/             # Dashboard, Schedule, CommitStyle, GitHub, Logs, Settings
        ├── components/        # Sidebar, Layout, ThemeToggle, DistributionEditor, ui/
        ├── hooks/useApi.ts    # React Query hooks
        └── lib/               # api.ts, theme.tsx, distribution.ts, utils.ts
```

## Adding a feature

1. Touch the relevant `backend/lambdas/<name>/handler.py` (or a
   `shared/*.py` module if it belongs there).
2. Add or update a test in `backend/tests/`. Run `uv run pytest`.
3. `python deploy.py` — it diffs IAM/Lambda env/code and updates only
   what changed. No manual zip step.
4. If the feature surfaces in the UI, update `frontend/src/lib/api.ts`
   and the relevant page; the Amplify branch redeploys on push.

## Adding an API route

1. Write a `def _my_handler(event, body) -> dict` in
   `backend/lambdas/api/handler.py`. Return a dict; the dispatcher wraps
   it as an API Gateway response.
2. Register it in `_ROUTES` at the bottom of the file:
   `("POST", "/my/path"): _my_handler,`.
3. If the route hits a new AWS resource, extend the API Lambda's IAM
   policy in `deploy.py` (`ensure_iam` block).
4. Add a typed wrapper in `frontend/src/lib/api.ts` and a
   `useQuery`/`useMutation` in `frontend/src/hooks/useApi.ts`.
5. `python deploy.py`. API Gateway uses a catch-all proxy integration,
   so no Gateway change is needed for new paths.

## Adding a Lambda env var

1. Add the key to `common_env` (shared by all three Lambdas) or to the
   per-Lambda dict in the `plans = [...]` list in `deploy.py`
   (`update_lambdas` section, around line 925).
2. Read it via `os.environ["MY_VAR"]` in the handler or shared module.
   Prefer wrapping the read in a small helper in `shared/` so tests can
   monkeypatch it.
3. `python deploy.py`. The deploy script diffs `Environment.Variables`
   and calls `UpdateFunctionConfiguration` when it changes.

## Repo rename / transfer auto-heal

The connected repo is stored with its numeric GitHub `id`, which is
stable across renames and ownership transfers. Every executor run
calls `shared/github.py:resolve_current`, which looks up the repo by
id, returns the canonical `(owner, name, default_branch, full_name)`,
and a `changed` flag. When it drifts, the caller persists the new
canonical dict back to the `GITHUB` row. Legacy configs without an `id`
fall back to `owner/name` lookup once and get promoted on first read.

## Design decisions worth knowing

- Single-tenant by design — one DynamoDB `pk="USER#default"`. No
  multi-tenant indirection anywhere.
- `urllib` over `requests` in `shared/github.py` so Lambdas ship as a
  flat zip with zero third-party deps and no Lambda layer.
- GitHub commit author is the user's `<id>+<login>@users.noreply.github.com`
  address so the contribution graph counts the commit.
- Cron at 12:00 UTC — morning hours for US/EU users, so a same-day
  vacation toggle still works before any commits fire.
- Sunlit-paper palette is expressed as CSS variables in
  `frontend/src/index.css`; light/dark mode flips the variable values,
  components never branch on theme.

## Future ideas

- Multi-repo: either a list of repos under the single user, or actual
  multi-tenancy (would touch every DDB key).
- GitHub webhook on push so a real human commit auto-marks the day as
  "already covered" instead of relying on the vacation toggle.
- Calendar integration: skip national holidays / detected PTO.
- Move the PAT to a GitHub App for finer scoping and auto-rotation.
