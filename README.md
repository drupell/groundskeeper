# Groundskeeper

Groundskeeper is a framework for handing an LLM a repository you own plus a
rough direction, then watching what it actually chooses to do with it. You
point it at a repo, write a short style prompt, and shape a per-day
commit-count curve. Once a day, an agent picks an eligible file, decides
between a creative edit (additive content matching your prompt) or a
destructive edit (removing or restructuring a small section), and commits
the result. Over time the project accumulates a corpus of those choices —
which files the agent kept reaching for, what tone it landed on, what it
left alone.

[![dev](https://github.com/OWNER/REPO/actions/workflows/dev.yml/badge.svg)](https://github.com/OWNER/REPO/actions/workflows/dev.yml)
[![main](https://github.com/OWNER/REPO/actions/workflows/main.yml/badge.svg?branch=main)](https://github.com/OWNER/REPO/actions/workflows/main.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)

## What it does

A single observation cycle looks like this. You connect a GitHub token and
a repo, write a style prompt that tells the agent what kinds of edits feel
right for this codebase, and sculpt a distribution curve for how many
commits a day are possible. Once a day at 12:00 UTC, a small Lambda samples
a commit count from your curve and schedules that many one-time triggers at
random times inside today's window.

As each scheduled time arrives, a second Lambda reads the repo, picks an
eligible file, and asks Amazon Bedrock (Nova Lite) to choose between a
creative or destructive edit and produce the patch plus commit message. The
result lands via the GitHub Contents API as an ordinary commit, authored by
the verified user behind your token. Every decision — file chosen, edit
mode, prompt used, model output, success or failure — gets a row in
DynamoDB. That log is the artifact: a record of how the agent behaved over
days and weeks against a single body of code.

The dashboard is a React app served from AWS Amplify behind basic auth. It's
where you connect the token, pick the repo, sculpt the curve, edit each
day's window, write the style prompt, and toggle vacation mode. A single
API Gateway endpoint forwards to one API Lambda, which reads and writes a
single DynamoDB table. Your token never leaves Secrets Manager. Everything
is tagged `project=groundskeeper`, runs on on-demand and pay-per-use
services, and is fully removable with the companion `teardown.py` script.

```
   ┌──────────────┐         ┌──────────────────┐
   │  Dashboard   │ ──API──▶│  api Lambda      │──┐
   │  (Amplify)   │         └──────────────────┘  │
   └──────────────┘                               │
                                                  ▼
   ┌──────────────────┐    ┌──────────────────┐  ┌───────────────┐
   │  EventBridge     │───▶│ orchestrator     │──│  DynamoDB     │
   │  Scheduler       │    │ Lambda (daily)   │  │  (single tbl) │
   │  (12:00 UTC)     │    └──────────────────┘  └───────────────┘
   └──────────────────┘             │                   ▲
            ▲                       ▼                   │
            │              ┌──────────────────┐         │
            └──────────────│ one-time rules   │         │
                           └──────────────────┘         │
                                    │                   │
                                    ▼                   │
                           ┌──────────────────┐         │
                           │ executor Lambda  │─────────┘
                           │ (per commit)     │
                           └──────────────────┘
                              │            │
                              ▼            ▼
                       ┌────────────┐  ┌──────────────┐
                       │ Bedrock    │  │ Secrets Mgr  │
                       │ Nova Lite  │  │ (GitHub PAT) │
                       └────────────┘  └──────────────┘
                              │
                              ▼
                       ┌────────────┐
                       │ GitHub API │
                       └────────────┘
```

| Component             | What it does                                          |
| --------------------- | ----------------------------------------------------- |
| `api` Lambda          | Backs every dashboard request behind API Gateway      |
| `orchestrator` Lambda | Plans each day's commits at 12:00 UTC                 |
| `executor` Lambda     | Runs one scheduled commit end-to-end                  |
| DynamoDB              | Single table for config, run records, and commit logs |
| Secrets Manager       | Stores your GitHub personal access token              |
| EventBridge Scheduler | Daily cron plus one-time per-commit rules             |
| Bedrock (Nova Lite)   | Generates each commit's content                       |
| Amplify               | Hosts the dashboard behind basic auth                 |

## What v0.1.0 is (and isn't)

This first release is the foundation: enough to run a real observation loop
end-to-end against one repo, with one model, on one AWS account. The arc
beyond that is real but deliberately not in this release.

**Shipped in v0.1.0:**

- One repo per deployment, owned by you, configured from the dashboard.
- One agent: Amazon Bedrock Nova Lite, behind a fixed two-mode prompt
  scaffold (creative vs destructive edit).
- One observer: a single dashboard user, basic-auth'd, with full read/write
  on schedule, prompt, curve, and vacation toggle.
- A complete per-commit log in DynamoDB — file picked, edit mode, prompt,
  model output, GitHub result.
- Fully tagged, fully removable AWS deployment via `deploy.py` and
  `teardown.py`.

**Deferred to later versions:**

- Multi-repo deployments and multi-agent comparisons.
- Pluggable model providers and richer prompt scaffolds (research-then-edit,
  plan-then-execute).
- Decision telemetry surfaced as dashboards on top of the commit log.
- Cross-model behavioral comparisons against the same repo.

**A note on the contribution graph.** Because the agent's commits are
authored by your verified GitHub user, GitHub counts them on your
contribution graph like any other commit. That's a side effect of how the
executor reaches GitHub, not the point of the project. The point is the
corpus of choices in the log. The disclaimers below take this seriously.

## Honest disclaimers

Please read these before deploying.

- **Every commit is LLM-authored.** Content and message both come from
  Bedrock Nova Lite, generated against the prompt you wrote and the file
  the agent picked. The commits are real — real SHAs, authored as the user
  behind your token — but they're not your work, and the whole purpose of
  this project is to observe the agent honestly. Be honest with yourself
  and anyone reading the repo about who wrote them.
- **The contribution graph counts them.** Groundskeeper exists to study
  agent behavior, not to game GitHub. But the commits are normal commits,
  so they show up on your graph. Don't point this at a repo where that
  misrepresents you — shared repos, work repos, or anything that implies
  human authorship matters. Use it on a personal repo you own and have
  flagged as an experiment.
- **Single-tenant by design.** v0.1.0 is one user, one dashboard password,
  one GitHub token, one repo, one model. Multi-repo, multi-agent, and
  hosted-service work belongs in later versions; PRs that bolt on
  multi-tenancy to this release are out of scope.
- **Cost.** Single-user usage typically lands under $1/month. Most of that
  is the flat Secrets Manager per-secret fee, not AI inference. Full
  breakdown is in [What it costs](#what-it-costs).
- **You own the resources.** Everything runs in your AWS account.
  `teardown.py` removes it all when you're done.

## Prerequisites

- An **AWS account** where you have admin (deploy creates IAM roles and
  policies).
- **Bedrock model access** for `amazon.nova-lite-v1:0` in the region you
  plan to deploy to. Turn it on in the
  [Bedrock console → Model access](https://docs.aws.amazon.com/bedrock/latest/userguide/model-access.html).
  `us-east-1` and `us-west-2` are safe defaults.
- A **GitHub personal access token (classic)** with the `repo` scope.
  Create one [here](https://github.com/settings/tokens/new?scopes=repo).
  You'll paste it into the dashboard, never into the script.
- **macOS or Linux.**
- **Python 3.12+** and [**uv**](https://docs.astral.sh/uv/#installation) on
  your `PATH`.
- **Node 20+** on your `PATH`. `deploy.py` builds the dashboard for you;
  Node just needs to be available.
- The **AWS CLI** configured locally (`aws configure`, SSO, or env vars).
  The deploy script uses whatever credentials it finds and confirms the
  target account with you before changing anything.

## First-time deploy

From the repo root:

```sh
# Install deploy.py's own dependencies into .venv/ at the repo root.
# uv reads pyproject.toml + uv.lock and resolves everything for you.
uv sync

# Run the guided deploy. --environment prod tags every resource as prod
# and names them groundskeeper-prod-*; use --environment dev for a parallel
# dev stack you can blow away independently.
uv run python deploy.py --environment prod
```

If you use AWS SSO, the script picks up your profile from the environment.
Export it before running if it isn't already your default:

```sh
# Refresh your SSO session, then point deploy.py at the right profile.
aws sso login --profile my-sso-profile
export AWS_PROFILE=my-sso-profile

uv run python deploy.py --environment prod
```

What you'll see, in order:

1. The detected AWS account and region, with a yes/no confirmation.
2. A preview of every IAM policy `deploy.py` is about to attach, with
   another yes/no confirmation. (Pass `--yes` on subsequent runs to skip
   this once you've reviewed it.)
3. Per-resource progress with a `→` while it's working and a `✓` when it
   succeeds. Idempotent — re-running reuses anything already there.
4. A prompt for the dashboard password. This becomes the Amplify basic-auth
   password for the user `admin`. Pick something memorable; you can rotate
   it later from the dashboard.
5. The frontend build (`npm ci && npm run build`), the upload to Amplify,
   and a wait for the deployment to go live.
6. A summary banner that looks roughly like:

   ```
   ✓ Dashboard is live at https://main.<app-id>.amplifyapp.com
     username: admin
     password: (the one you just set)
   ```

Open the URL, log in, and continue with [Using the dashboard](#using-the-dashboard).

## Using the dashboard

Set things up in the order below. You can come back and tune anything at
any time — every change saves immediately to DynamoDB and takes effect on
the next orchestrator run.

**Step 1 — connect GitHub.** Sidebar → _GitHub_. Paste your personal access
token and click _Verify token_. A preview card shows the avatar, name,
email, and scopes the token carries. If the `repo` scope is missing, the
dashboard tells you and refuses to save. Click _Save this token_ — it's
written to Secrets Manager, never to DynamoDB or logs. Then in the
_Repository_ card, paste the URL (or `owner/repo`) of the repo you want to
commit to and click _Validate & save_. The card shows the default branch,
last commit, and a warning if your token can't push. If you rename the repo
on GitHub later, Groundskeeper detects the redirect and updates the stored
repo identifier automatically on the next read or run — no manual fix.

**Step 2 — shape the schedule.** Sidebar → _Schedule_. Set the _min/max
commits per day_ (say 0–4) and save. Then sculpt the _distribution curve_:
drag the handles, or pick a preset (Uniform, Bell, Left-skewed,
Right-skewed). The histogram and the "you'll average ~X commits/day"
readout update live. In _Weekly window_, toggle each day on or off and set
its start/end time; off days are dimmed. If a day's window is too short to
fit the max commits at the configured gap, an amber banner names the days
that will get auto-clamped.

**Step 3 — pick a voice.** Sidebar → _Commit Style_. Write the prompt that
Nova Lite follows for every commit — tone, voice, what kinds of edits feel
right for this repo. Set the _destructive-commit probability_ (the chance
any given commit removes code rather than adds it) and the _line range_ per
commit.

**Step 4 — settings.** Sidebar → _Settings_. Confirm your timezone (the
orchestrator plans each day's window in local time), tune the
between-commits gap, toggle light or dark theme, and rotate the dashboard
password if you'd like.

**The Dashboard page.** The home page surfaces the _Vacation Mode_ card
(pause indefinitely or until a date you pick — the orchestrator exits
cleanly while it's on), today's plan card with a relative-date label
("today" / "tomorrow"), status cards, the most recent run, and a recent
activity feed. The _Test & preview_ card has two buttons: _Run a test
commit now_ fires the executor immediately, and _Preview tonight's plan_
runs the orchestrator in dry-run mode. There's also a _Run scheduler now_
button if you want the planner to re-plan today right away rather than
waiting for the next 12:00 UTC tick.

**The Logs page** shows every commit attempt — success or failure — with
pagination via _Load more_.

## What it costs

You pay AWS directly. For a single user committing a few times a day on
us-east-1 list prices, the honest breakdown is:

| Service               | What it's for                          | Typical monthly cost                                                               |
| --------------------- | -------------------------------------- | ---------------------------------------------------------------------------------- |
| Bedrock — Nova Lite   | Commit content generation              | ~$0.0006 per commit (input + output combined); ~$0.05–$0.10/month at 5 commits/day |
| Lambda                | Orchestrator + executor invocations    | ~$0 — well inside the Free Tier at this scale                                      |
| DynamoDB (on-demand)  | Config + run/commit logs               | ~$0 — a few hundred small reads/writes a month                                     |
| EventBridge Scheduler | Daily cron + one-time per-commit rules | ~$0 — well inside the free allowance                                               |
| API Gateway           | Dashboard ↔ backend                    | ~$0 — only fires while the dashboard is open                                       |
| Amplify Hosting       | Serving the dashboard                  | ~$0.15/GB-month stored + ~$0.01/GB served; the bundle is a few hundred KB          |
| Secrets Manager       | Stores your GitHub token               | ~$0.40 flat per-secret fee — the largest single line item                          |
| CloudWatch Logs       | Structured Lambda logs                 | ~$0–$0.05                                                                          |
| **Total**             |                                        | **typically well under $1/month at ≤5 commits/day**                                |

Heavier use — say 10 commits/day against large files — pushes the Bedrock
line a few cents higher; everything else stays effectively flat at
single-user volume. Prices change and vary by region; check the
[AWS Pricing Calculator](https://calculator.aws/) and your own bill, and
run `teardown.py` when you're done so nothing lingers.

## Redeploying after code changes

The deploy command is idempotent — re-run the same one-liner:

```sh
# Re-runs the full provisioning flow. Existing resources are reused;
# Lambda zips and the frontend bundle are rebuilt and re-uploaded.
uv run python deploy.py --environment prod
```

Pass `--yes` to skip the IAM policy preview confirmation on re-runs once
you've reviewed it.

## Tearing down

```sh
# Removes every resource deploy.py created, in dependency order. Reads
# .groundskeeper-deploy-state.json; falls back to name-based discovery
# for anything the state file doesn't know about.
uv run python teardown.py --environment prod
```

The Secrets Manager secret is **soft-deleted with a 7-day recovery window**
by default, so you can change your mind. If you're redeploying immediately
and want the same secret name available, add `--force-secret-delete`.

Other useful flags:

- `--yes` — skip the final confirmation (the resource list is still
  printed).
- `--keep-logs` — leave the CloudWatch log groups behind.
- `--region us-west-2` — override the region from the state file.

## Customizing

**Commit style.** The _Commit Style_ page is the main creative control.
The prompt is fed verbatim to Nova Lite alongside the file's existing
content. The destructive-probability slider sets how often a commit removes
code instead of adding it. The line-range card sets the minimum and maximum
number of lines a single commit can touch.

**The distribution curve (hero feature).** The curve on the _Schedule_
page sets the probability of each possible per-day commit count, between
your min and max. A bell curve clusters most days around the middle of the
range; a left-skewed curve makes light days the norm with the occasional
heavier one; a right-skewed curve does the opposite; uniform makes every
count equally likely. Sculpting this — rather than picking a fixed
"3 commits a day" — is what gives the agent a varied cadence to operate
against instead of a rigid one.

**Per-day window.** Each weekday can be on or off and has its own
start/end time. Off days are dimmed in the editor. The orchestrator only
plans commits inside the window for the day it's planning.

**Vacation mode.** The _Dashboard_ page's _Vacation Mode_ card pauses
the planner indefinitely or until a date you pick. Active days are
highlighted in warm amber. One click resumes.

**Light / dark theme.** Toggle in the header. The theme preference is
saved per-browser; the rest of the dashboard's look comes from a small set
of semantic CSS-variable tokens in `frontend/src/index.css` that define
both modes.

## Testing without waiting

The orchestrator only fires at 12:00 UTC and schedules commits at random
future times, so you don't want to wait on the cron to confirm everything's
wired up.

**Easiest: the dashboard.** On the _Dashboard_ page, the _Test & preview_
card has _Run a test commit now_ (fires the executor immediately — a real
commit lands within seconds) and _Preview tonight's plan_ (runs the
orchestrator in dry-run mode and shows the target day, sampled count, and
planned local times, **without** creating any schedules or writing a run
record). The _Run scheduler now_ button on the same page runs the
orchestrator for real, against today.

**From the CLI.** Same flows, via the AWS CLI. Invoke the executor
end-to-end:

```sh
# Picks an eligible file, asks Bedrock for an edit, commits it via the
# GitHub Contents API, logs the result. Preconditions: token saved, repo
# configured, config in place (i.e. you've finished dashboard setup).
aws lambda invoke \
  --function-name groundskeeper-executor \
  --payload '{}' \
  --cli-binary-format raw-in-base64-out \
  /dev/stdout
```

Or run the orchestrator in dry-run mode:

```sh
# Returns what it WOULD schedule for today; doesn't create rules or write
# a run record. Useful for sanity-checking your curve / window / gap.
aws lambda invoke \
  --function-name groundskeeper-orchestrator \
  --payload '{"dry_run": true}' \
  --cli-binary-format raw-in-base64-out \
  /dev/stdout
```

**Pure-logic tests.** The distribution sampling, time-slot planning, file
eligibility, repo-URL parsing, config validation, and prompt-cleanup logic
all have unit tests that run with no AWS dependencies:

```sh
uv run pytest -q
```

## Troubleshooting

**Bedrock model access denied (`AccessDeniedException` from the executor).**
You haven't enabled `amazon.nova-lite-v1:0` in your region yet. Go to the
[Bedrock console → Model access](https://docs.aws.amazon.com/bedrock/latest/userguide/model-access.html)
in the region you deployed to and request access. It's usually instant.

**Token rejected, or saved but the _Repository_ card warns it can't push.**
The token is probably missing the `repo` scope (private repos need the
full `repo` scope, not just `public_repo`). Mint a new one
[here](https://github.com/settings/tokens/new?scopes=repo) with `repo`
checked, paste it into _GitHub → Verify token_, and re-save.

**You renamed the repo on GitHub.** Nothing to do. The GitHub client
follows the redirect, resolves the new `owner/repo`, and updates the
stored value on the next read or run. The _Repository_ card will show the
new name the next time it loads.

**SSO token expired during a deploy.** Refresh and re-run — `deploy.py`
is idempotent and picks up where it left off:

```sh
aws sso login --profile my-sso-profile
uv run python deploy.py --environment prod
```

**The cron didn't fire.** Check CloudWatch Logs for the orchestrator:

```sh
# Tail the last hour of orchestrator logs.
aws logs tail /aws/lambda/groundskeeper-orchestrator --since 1h
```

Common causes: Vacation Mode is on, today isn't an enabled weekday, or
today's window has already fully passed (in which case the orchestrator
plans for _tomorrow_ — the dashboard's "today's plan" card shows which
date it's planning for).

**A request returns "Permanently moved" or 301.** That's the GitHub
redirect auto-heal path firing. It's expected and harmless; the request
is automatically retried against the new location.

## How it works (architecture)

The three Lambdas share a single `backend/shared/` package that handles
config + validation, DynamoDB access, the GitHub REST client, Bedrock
invocation, Secrets Manager, the distribution sampler, the time-slot
planner, structured logging, typed errors, the Amplify password rotation
helper, and the EventBridge Scheduler helper.

The deeper version — single-table key design, every Lambda env var, full
API Gateway routing table, every IAM policy — lives in
[CONTRIBUTING.md](./CONTRIBUTING.md) alongside the development setup.

## Contributing

This is a single-tenant personal tool. Bug fixes, docs improvements, and
small features that fit that scope are welcome. Refactors toward
multi-tenancy or a hosted service aren't. See
[CONTRIBUTING.md](./CONTRIBUTING.md) for the dev loop, the test setup, and
the PR checklist.

## License

[MIT](./LICENSE) — short, permissive, no warranty. Do what you want; don't
blame me.
