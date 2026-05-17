# Groundskeeper

A personal tool that automates realistic-looking GitHub contribution activity
on a repo you own. Designed to be deployed once into your own AWS account,
controlled from a password-protected dashboard, and torn down with a single
command when you're done.

> **Status: feature-complete dev preview.** The deploy + teardown scripts,
> the shared backend library, all three Lambdas (API, orchestrator,
> executor), and every dashboard page are wired and render real data:
> Dashboard (with the prominent Vacation Mode control), Schedule (the hero
> curve editor + editable per-day windows with a live over-constraint
> warning), Commit Style, GitHub, Logs, and Settings. The full spec is
> implemented end-to-end. See [What works today](#what-works-today) below.

---

## How it will work

- Daily **Orchestrator Lambda** reads your config from DynamoDB, samples your
  shaped commit-count distribution for today, and creates one-time
  EventBridge Scheduler rules at randomized times inside your active window.
- Each fired schedule invokes the **Commit Executor Lambda**, which picks a
  file from your repo, asks **Amazon Bedrock (Nova Lite)** for a creative or
  destructive edit, and writes the result back via the GitHub Contents API
  as a commit authored by you.
- The **dashboard** lets you configure your repo, schedule, distribution
  shape, commit-style prompt, and vacation mode — all backed by an API
  Gateway → Lambda → DynamoDB stack in your own account.

---

## Honest disclaimers

Read these before deploying.

- **GitHub Terms of Service.** Automating commits to inflate your contribution
  graph is in tension with GitHub's spirit-of-use guidelines. This tool only
  ever touches a single repository that you own, with a PAT you control. Use
  it on a personal repo you don't mind looking artificial. Don't point it at
  shared/work repos. Don't use it to misrepresent yourself professionally.
- **AWS cost.** Realistic steady-state cost for a single user is well under
  **$1/month** — and most of that is a flat Secrets Manager fee, not AI usage.
  See [What it costs](#what-it-costs) for the line-by-line breakdown.
- **You own the resources.** Everything runs in your AWS account under
  resources tagged `project=groundskeeper`. `teardown.py` will remove them.

---

## Prerequisites

- **AWS account** with permissions to create IAM roles, Lambda, DynamoDB,
  API Gateway, EventBridge, Secrets Manager, S3, and Amplify.
- **AWS CLI** configured locally (`aws configure`, SSO, or env vars). The
  deploy script uses whatever credentials it finds and confirms the target
  account with you before doing anything.
- **Bedrock model access** for `amazon.nova-lite-v1:0` in your chosen region.
  Enable in the Bedrock console → *Model access*. Deploy will warn you if it
  isn't enabled.
- **Python 3.10+** (the deploy script creates its own virtualenv).
- **Node 18+** on your `PATH` — only needed for the frontend build step.
- **GitHub PAT** with the `repo` scope. Create at
  https://github.com/settings/tokens/new. You'll paste it into the dashboard,
  not into the script.

---

## What works today

- `deploy.py` — full guided AWS provisioning: IAM (with policy preview),
  DynamoDB, Secrets Manager, S3 artifacts bucket, three Lambdas, API
  Gateway with API key + CORS, EventBridge daily rule, Amplify app with
  basic-auth password protection. Idempotent and re-runnable. Each Lambda
  zip bundles `backend/shared/` under `shared/` so handlers can do
  `from shared.foo import bar`.
- `.groundskeeper-deploy-state.json` (created on first run) — every resource
  ID/ARN, used by future `teardown.py`.
- `backend/shared/` — the cross-Lambda library: structured JSON logging
  (`log`), typed errors (`errors`), API-Gateway response helpers
  (`responses`), DynamoDB single-table helpers (`ddb`), Secrets-Manager-
  backed PAT storage (`secrets`), config schema + validation (`config`),
  distribution-curve sampling (`distribution`), commit-time slot planning
  with gap clamping (`timeplan`), a PAT-authed GitHub REST client
  (`github`), repo-file eligibility rules (`files`), Bedrock Nova Lite
  invocation + prompt builders (`bedrock`), and Amplify basic-auth
  password rotation (`amplify`).
- `backend/lambdas/api/handler.py` — the dashboard API Lambda, fully
  implemented. Single `{proxy+}` Lambda routes:
  `GET/PUT/PATCH /config`, `POST /github/verify`, `PUT/DELETE /github/pat`,
  `GET /github/status`, `GET/PUT /github/repo`, `GET /logs`, `GET /runs`,
  `GET /status`, `PUT /vacation`, `POST /password`, `GET /health`. Every
  request emits structured JSON logs with `aws_request_id` and every
  error maps cleanly to its HTTP status.
- `backend/lambdas/orchestrator/handler.py` — daily planner. Honors vacation
  mode, resolves the target local date (today or tomorrow depending on
  whether today's window is still ahead), samples the commit count from the
  user-shaped curve, plans times with gap constraints, and creates one
  EventBridge Scheduler one-time rule per commit
  (`ActionAfterCompletion=DELETE` keeps cleanup automatic). Persists a
  `RUN#<local_date>` record for the dashboard.
- `backend/lambdas/executor/handler.py` — per-commit worker. Picks an
  eligible random file from the configured repo, asks Bedrock Nova Lite
  for either a creative or destructive edit (probability configured), and
  writes the result back via the GitHub Contents API as a commit authored
  by the verified GitHub user. Logs success or failure to DynamoDB as
  `LOG#<iso>#<rand>`.

The **frontend** is feature-complete: Vite 6 + React 19 + TypeScript + Tailwind
4 + React Query, with ESLint + Prettier pinned at the same quality bar as the
Python tooling. Six wired pages — Dashboard, Schedule, Commit Style, GitHub,
Logs, Settings — all backed by real API data through React Query hooks.
`npm run build` is clean (≈ 363 KB JS / 27 KB CSS, gzipped 110 / 5.8).

Page summaries:

- **Dashboard** — a prominent **Vacation Mode** card (enable indefinitely or
  until a date, warm amber active state, one-click "Resume now"), status
  cards, latest run summary, recent activity feed.
- **Schedule** — editable commit-count range, the hero **distribution curve
  editor** (SVG with drag handles per integer count, four preset shapes —
  Uniform / Bell / Left-skewed / Right-skewed, "Average X commits/day"
  readout, live probability histogram), and an editable **weekly window**
  (per-day on/off toggle + start/end time pickers, off days dimmed) with a
  live amber warning when a day's window can't fit the max commits at the
  configured gap. Math in `lib/distribution.ts` mirrors
  `backend/shared/distribution.py` exactly.
- **Commit Style** — Nova Lite prompt textarea, destructive-probability
  slider with frequency readout, and a line-range card.
- **GitHub** — paste → verify → preview card → save PAT flow, plus a live
  repository card with branch, last-commit, push-permission warning, and an
  "Open on GitHub" link.
- **Logs** — paginated commit log with "Load more".
- **Settings** — timezone picker (browser-wide `Intl.supportedValuesOf`
  with a local-time sanity preview), commit-gap sliders, and Amplify
  basic-auth password rotation.

The dashboard now implements the spec end-to-end. The only remaining work is
real-world validation: a live `deploy.py` run against an AWS account with
Bedrock Nova Lite model access enabled. See
[Testing without waiting](#testing-without-waiting) for how to verify the
commit pipeline in seconds instead of waiting on the nightly cron.

---

## Running the deploy

From the project root:

```sh
python3 deploy.py
```

On first run the script will:

1. Create `.venv-deploy/` and install its own dependencies into it, then
   re-exec itself inside the venv.
2. Confirm the AWS account + region using your local credentials.
3. Show you exactly which IAM policies it's about to attach, and ask before
   applying them.
4. Provision every resource, printing a `→` for each step and a `✓` when
   it succeeds.
5. Prompt for the dashboard password, build the frontend (if present),
   upload it to Amplify, and wait for the deployment to go live.
6. Print a summary table with the dashboard URL and all resource IDs.

Re-running is safe — existing resources are reused.

Useful flags:

- `--region us-west-2` — override the region.
- `--yes` — skip the IAM policy preview confirmation (you've already seen
  it on a prior run).
- `--skip-frontend` — provision AWS infra without building/deploying the
  React app (useful while the dashboard is in development).

---

## Using the dashboard

After `deploy.py` finishes it prints your dashboard URL
(`https://main.<app-id>.amplifyapp.com`). Open it and you'll get a browser
basic-auth prompt — log in with username **`admin`** and the dashboard
password you chose during deploy.

First-run setup, in order:

1. **GitHub** (sidebar → *GitHub*).
   - Paste your Personal Access Token and click **Verify token**. A preview
     card shows the avatar, name, email, and scopes the token carries. If the
     `repo` scope is missing it tells you so and blocks the save.
   - Click **Save this token**. It's written to AWS Secrets Manager — never
     to DynamoDB, environment variables, or logs.
   - In the **Repository** card, paste the URL (or `owner/repo`) of a repo
     **you own and don't mind looking automated**. Click **Validate & save**.
     The card then shows the default branch, last commit, and a warning if
     your PAT can't push.
2. **Schedule** (sidebar → *Schedule*).
   - Set the **min/max commits per day** (e.g. 0–4) and **Save**.
   - **Sculpt the distribution curve**: drag the points, or click a preset
     (Uniform / Bell / Left-skewed / Right-skewed). The histogram and the
     "you'll average ~X commits/day" readout update live.
   - In **Weekly window**, toggle each day on/off and set its start/end time.
     Off days are dimmed. If a window is too short to fit your max commits at
     the configured gap, an amber banner tells you which days will be
     auto-clamped.
3. **Commit Style** (sidebar → *Commit Style*).
   - Write the creative prompt that's fed to Nova Lite (tone, voice, what to
     add). Set the **destructive-commit probability** and the **line range**.
4. **Settings** (sidebar → *Settings*).
   - Confirm your **timezone** (the orchestrator plans the day's window in
     local time), tune the **commit gap**, and rotate the dashboard password
     if you want.

That's it. The orchestrator runs every night at **00:05 UTC**: it reads your
config, samples a commit count for the upcoming local day, and schedules that
many commits at random times inside your window. As they fire you'll see them
appear on GitHub and on the **Dashboard** / **Logs** pages.

To pause without tearing anything down, use the **Vacation Mode** card at the
top of the Dashboard — pause indefinitely or until a date, and resume with one
click. The nightly planner exits cleanly while it's on.

---

## Project layout

```
Groundskeeper/
├── deploy.py                     # Guided AWS provisioning
├── teardown.py                   # Guided AWS teardown (mirror of deploy.py)
├── requirements-deploy.txt       # boto3, rich, questionary
├── backend/
│   ├── lambdas/
│   │   ├── orchestrator/         # Nightly planner (full)
│   │   ├── executor/             # Per-commit worker (full)
│   │   └── api/                  # Dashboard API behind API GW (full)
│   └── shared/                   # Cross-Lambda utilities
└── frontend/                     # Vite + React + Tailwind + shadcn/ui
    ├── src/{components,pages,hooks,lib}/
    └── public/
```

---

## Development tooling

### Python

Python code is formatted with **Black** and linted with **Ruff**, both pinned
in `requirements-dev.txt`. The settings live in `pyproject.toml`.

`pytest` (+ `boto3`) is also pinned in `requirements-dev.txt`; the suite lives
in `backend/tests/` and covers the pure logic with no AWS calls.

```sh
python3 -m venv .venv-dev
.venv-dev/bin/pip install -r requirements-dev.txt

.venv-dev/bin/black deploy.py teardown.py backend/      # format
.venv-dev/bin/ruff check deploy.py teardown.py backend/ # lint
.venv-dev/bin/python -m pytest -q                       # test (52 cases)
```

### Frontend

The frontend uses **Prettier** for formatting and **ESLint** (flat config,
with `@typescript-eslint` + `eslint-plugin-react-hooks` +
`eslint-plugin-react-refresh`) for linting, at the same strict-but-opinionated
bar as the Python setup. Both are pinned in `frontend/package.json`.

```sh
cd frontend
npm install

npm run format         # prettier --write .
npm run lint           # eslint .
npm run typecheck      # tsc --noEmit
npm run build          # tsc --noEmit && vite build
npm run dev            # vite dev server
```

---

## Teardown

```sh
python3 teardown.py
```

`teardown.py` is the mirror image of `deploy.py`. It self-bootstraps the
same virtualenv, reads `.groundskeeper-deploy-state.json`, falls back to
name-based discovery for anything missing from state, and prints the full
list of resources it found before asking you to confirm. On confirmation
it removes — in dependency order — the EventBridge daily rule, any
leftover one-time scheduler rules, all three Lambdas, the API Gateway
REST API + API key + usage plan, the DynamoDB table, the GitHub PAT
secret, the S3 artifacts bucket (with all object versions), the IAM
roles + inline policies, the Amplify app, and the CloudWatch log groups.
Re-running is safe: each delete swallows `NotFound` errors, so a partial
teardown can resume cleanly.

Useful flags:

- `--yes` — skip the final confirmation (the resource list is still shown).
- `--force-secret-delete` — permanently delete the Secrets Manager secret
  instead of scheduling a 7-day recovery window (use if you intend to
  redeploy immediately).
- `--keep-logs` — leave CloudWatch log groups behind.
- `--region us-west-2` — override the region from the state file.

---

## What it costs

Everything runs in **your** AWS account, so you pay AWS directly. For a single
user committing a few times a day, here's the honest breakdown (us-east-1
list prices, late-2025; your region and the AWS Free Tier may make several of
these literally $0):

| Service | What it's used for | Typical monthly cost |
|---|---|---|
| **Secrets Manager** | Stores your GitHub PAT | **~$0.40** (flat per-secret fee — the single biggest line item) |
| **Bedrock — Nova Lite** | Generating each commit's content | **~$0.05–$0.50** (scales with file size × commits/day; Nova Lite is one of the cheapest models) |
| **Lambda** | Orchestrator + executor runs | ~$0.00 (≈150 short invocations/month — comfortably inside Free Tier) |
| **DynamoDB** | Config + run/commit logs (on-demand) | ~$0.00 (a few hundred tiny reads/writes) |
| **API Gateway** | Dashboard ↔ backend | ~$0.00 (only while you have the dashboard open) |
| **EventBridge Scheduler** | One-time commit triggers | ~$0.00 (far inside the free allowance) |
| **Amplify Hosting** | Serving the dashboard | ~$0.00 (static site, low traffic; Free Tier) |
| **S3** | Lambda/frontend build artifacts | ~$0.00 (a few MB) |
| **CloudWatch Logs** | Structured Lambda logs | ~$0.00–$0.05 (tiny JSON volume) |
| **Total** | | **typically well under $1/month** |

Heavier use (say 10 commits/day against large files) pushes the Bedrock line
toward **$1–$2/month**; everything else stays effectively free at single-user
volume. These are estimates — prices change and vary by region. Check the
[AWS Pricing Calculator](https://calculator.aws/) and your own bill, and run
`teardown.py` when you're done so nothing lingers.

---

## FAQ

**Will this violate GitHub's Terms of Service?**

Honest answer: it's in tension with the *spirit* of GitHub's rules, even
though the mechanics are ordinary API usage. Using the Contents API with your
own PAT to commit to your own repo is exactly what that API is for — the
automation itself isn't the problem. The problem is what the contribution
graph then implies. GitHub's Acceptable Use Policies prohibit "inauthentic"
activity and activity intended to deceive; manufacturing a green graph to make
recruiters or employers believe you were coding when you weren't is squarely
the kind of thing those policies are aimed at.

So: as a personal toy, a learning project for event-driven AWS, or commits to
a throwaway repo you're honest about — it harms no one. As profile padding you
present to employers — it's dishonest, a bad idea reputationally regardless of
any policy, and could get contributions or the account actioned. This tool
doesn't try to evade detection and we don't recommend trying to. Use it on a
repo you own, and don't misrepresent yourself.

**Are the commits "real"?**

Yes. They're genuine commits with real SHAs and real file changes, authored as
your verified GitHub identity, written through the GitHub Contents API. The
contribution graph counts them like any other commit. Whether they look
*plausible* on inspection depends on your prompt and the repo.

**Can I use a private repo?**

Yes. Private-repo commits count toward your graph only if you enable
*Settings → Profile → Private contributions* on GitHub. Groundskeeper only
ever touches the single repo you configure.

**Is my PAT safe?**

It's stored only in AWS Secrets Manager in your own account — never in
DynamoDB, environment variables, or logs. The dashboard sits behind Amplify
basic auth. You can disconnect the token from the GitHub page anytime, and
`teardown.py` deletes the secret entirely.

**Which region should I pick?**

One where Amazon Bedrock offers Nova Lite *and* you've enabled model access
for it (Bedrock console → *Model access*). `us-east-1` and `us-west-2` are
safe choices. `deploy.py` warns you if model access isn't enabled.

**I deployed but no commits are appearing.**

Walk the checklist: (1) Bedrock model access enabled for `amazon.nova-lite-v1:0`?
(2) PAT saved and showing the `repo` scope on the GitHub page? (3) Repo
configured and the card shows push access? (4) Vacation Mode off? (5) Has the
nightly orchestrator (00:05 UTC) actually run yet, and is today an enabled day
with a window still ahead? The **Logs** page and CloudWatch
(`/aws/lambda/groundskeeper-*`) show exactly what happened. You don't have to
wait for the nightly cron to test — see
[Testing without waiting](#testing-without-waiting).

**How do I stop it temporarily vs. permanently?**

Temporarily: Vacation Mode on the Dashboard. Permanently: `python3 teardown.py`,
which removes every resource it created.

---

## Testing without waiting

The orchestrator only fires at 00:05 UTC and schedules commits at *random*
future times, so you don't want to wait on the cron to see whether everything
works. The fastest end-to-end check, with **zero code changes**, is to invoke
the executor Lambda directly — it runs the full real flow (pick a file → ask
Bedrock → commit → log):

```sh
aws lambda invoke \
  --function-name groundskeeper-executor \
  --payload '{}' \
  --cli-binary-format raw-in-base64-out \
  /dev/stdout
```

Within a few seconds you should see a new commit on your repo and a new entry
on the dashboard **Logs** page. (Preconditions: PAT saved, repo configured,
config present — i.e. you've done the dashboard setup above.)

**From the dashboard (no CLI needed).** The **Dashboard** has a *Test &
preview* card with two buttons:

- **Run a test commit now** — invokes the executor asynchronously; the
  resulting commit shows up in *Recent activity* and on the **Logs** page
  within a few seconds (the card auto-refreshes).
- **Preview tonight's plan** — runs the orchestrator in **dry-run** mode and
  shows exactly what it *would* schedule (target day, sampled count, the
  planned local times) **without creating any schedules or writing a run
  record**. Great for sanity-checking your curve/window/gap settings.

**Dry-run from the CLI**, equivalently:

```sh
aws lambda invoke --function-name groundskeeper-orchestrator \
  --payload '{"dry_run": true}' --cli-binary-format raw-in-base64-out /dev/stdout
```

**Unit tests.** The pure logic (distribution sampling, time-slot planning,
file eligibility, repo-URL parsing, config validation, prompt cleanup) has a
`pytest` suite with no AWS dependencies:

```sh
.venv-dev/bin/pip install -r requirements-dev.txt
.venv-dev/bin/python -m pytest -q
```
