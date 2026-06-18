# Release checklist

One-time checklist for the maintainer to walk through **before** pushing the public repo's first release. Work top-to-bottom; do not skip sections.

## Code + tests

- [ ] `uv run black --check backend/ deploy.py teardown.py`
- [ ] `uv run ruff check backend/ deploy.py teardown.py`
- [ ] `uv run pytest -q` (target: all green, ≥60 cases)
- [ ] `cd frontend && npm ci`
- [ ] `cd frontend && npx prettier --check src/`
- [ ] `cd frontend && npx tsc --noEmit`
- [ ] `cd frontend && npx eslint .`
- [ ] `cd frontend && npm run build` (should produce `dist/` with no warnings)
- [ ] Manually verify a dev deploy works end-to-end: `python deploy.py --env dev` → set PAT in Secrets Manager → configure repo in dashboard → click "Run scheduler now" → confirm real commit lands on the test repo within ~60s.
- [ ] Tear down the dev stack after validation: `python teardown.py --env dev`

## Security

- [ ] `uv run bandit -r backend/ deploy.py teardown.py` (no HIGH severity findings)
- [ ] `uv run pip-audit` (no unresolved CVEs)
- [ ] `cd frontend && npm audit --audit-level=moderate` (no moderate+ findings)
- [ ] `gitleaks detect --source . --verbose` on the working tree (no leaks)
- [ ] CodeQL: enabled in GitHub repo settings (Security → Code scanning → Set up → Default)
- [ ] Confirm NO personal AWS account ids / ARNs / dashboard URLs are in any committed file:
  - [ ] `git grep "845517756240"` returns nothing
  - [ ] `git grep "d1w4l2zuc5eoku"` returns nothing (Amplify app id)
  - [ ] `git grep -E "[a-z0-9]+\.execute-api\.us-east-1\.amazonaws\.com"` returns nothing (the generic hostname in docs is fine; specific subdomains are not)
  - [ ] `git grep -E "arn:aws:[a-z]+:[a-z0-9-]*:[0-9]{12}:"` returns nothing

## Privacy + secrets

- [ ] `.gitignore` protects deploy state and prod env file:
  - [ ] `git check-ignore -v .groundskeeper-deploy-state.json`
  - [ ] `git check-ignore -v frontend/.env.production`
- [ ] No `.env*` files staged: `git ls-files | grep -E '\.env'` returns nothing (except `.env.example` if present)
- [ ] PAT scrubbed from any test fixture: `git grep -E 'ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{82}'` returns nothing
- [ ] Paranoid history sweep: `git log --all --full-history -p | grep -iE 'ghp_|github_pat_|aws_secret_access_key|AKIA[0-9A-Z]{16}'` returns nothing
- [ ] No Secrets Manager secret names tied to your personal account in code (search for `groundskeeper-prod-github-token` etc. is fine — those are generic names — but `arn:aws:secretsmanager:...:845517756240:...` is not)

## Docs

- [ ] `README.md` renders cleanly on GitHub (use the GitHub web "Preview" before pushing, or `gh repo view --web` against a private staging fork)
- [ ] `ARCHITECTURE.md` links resolve (TODO: confirm file exists; if not, either create it or remove from checklist)
- [ ] `LICENSE` present, MIT, correct year (2026) and name (Dave Drupell — TODO: confirm exact name to use)
- [ ] `CONTRIBUTING.md` present (TODO: confirm file exists or create stub before release)
- [ ] No AI-tool mentions in any committed file:
  - [ ] `git grep -il -E 'claude|anthropic|cursor|copilot|gpt-[0-9]|chatgpt|openai'` returns nothing
- [ ] No TODO/FIXME comments that reference internal-only context: `git grep -nE 'TODO|FIXME|XXX|HACK'` reviewed line-by-line

## AWS

- [ ] Production deploy is healthy:
  - [ ] EventBridge Scheduler shows the daily 12:00 UTC rule as `ENABLED`
  - [ ] Most recent 3 executor invocations in CloudWatch Logs ended without error
  - [ ] DynamoDB has no schedule items stuck with `status=running` older than 1 hour
- [ ] All resources tagged with both `project=groundskeeper` AND `environment=prod`:
  - [ ] `aws resourcegroupstaggingapi get-resources --tag-filters Key=project,Values=groundskeeper Key=environment,Values=prod --region us-east-1` lists every Lambda, DDB table, secret, scheduler, and Amplify app
- [ ] Bedrock model access enabled in the deploy region:
  - [ ] `aws bedrock list-foundation-models --region us-east-1 --by-provider amazon` includes `amazon.nova-lite-v1:0`
  - [ ] Model access shows "Access granted" in the Bedrock console
- [ ] Cost in AWS console for the last 7 days is within expected band (under $1):
  - [ ] Cost Explorer filtered by tag `project=groundskeeper`

## CI deploy bootstrap (one-time per AWS account)

CI is the only path to dev or prod. Wire it up before any merge to `dev` or `main` can actually deploy. Repeat the AWS half **separately** in each account (dev and prod).

### A) Create the GitHub OIDC provider in each AWS account

Once per account. AWS will trust GitHub Actions to vend short-lived credentials — no long-lived AWS access keys leave AWS.

```sh
# In each AWS account (dev and prod), as a user with IAM admin:
aws iam create-open-id-connect-provider \
  --url https://token.actions.githubusercontent.com \
  --client-id-list sts.amazonaws.com \
  --thumbprint-list 6938fd4d98bab03faadb97b34396831e3780aea1
```

(Thumbprint is the cert chain for `token.actions.githubusercontent.com` — verify against [GitHub's docs](https://docs.github.com/en/actions/deployment/security-hardening-your-deployments/configuring-openid-connect-in-amazon-web-services) at the time you run this.)

### B) Create a deploy role per environment

In **each** account, create a role the CI workflow can assume. Trust policy must scope `sub` to the exact repo + branch — never accept any-branch OIDC tokens.

`trust-policy.json` (substitute the values in `<…>`):

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "Federated": "arn:aws:iam::<ACCOUNT_ID>:oidc-provider/token.actions.githubusercontent.com" },
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": { "token.actions.githubusercontent.com:aud": "sts.amazonaws.com" },
      "StringLike": { "token.actions.githubusercontent.com:sub": "repo:<GITHUB_USER>/<REPO_NAME>:ref:refs/heads/<BRANCH>" }
    }
  }]
}
```

- In the dev account: set `<BRANCH>` to `dev`.
- In the prod account: set `<BRANCH>` to `main`.

```sh
aws iam create-role --role-name groundskeeper-ci-deploy --assume-role-policy-document file://trust-policy.json
aws iam attach-role-policy --role-name groundskeeper-ci-deploy --policy-arn arn:aws:iam::aws:policy/AdministratorAccess
```

`deploy.py` creates IAM, Lambda, DynamoDB, Secrets Manager, EventBridge Scheduler, S3, API Gateway, and Amplify resources, so the deploy role needs admin-ish reach. Narrow to a custom policy in a follow-up; for the first release admin keeps friction low.

### C) Set GitHub repository secrets

In the public repo: **Settings → Secrets and variables → Actions → Secrets**.

| Name | Value |
|---|---|
| `AWS_DEV_DEPLOY_ROLE_ARN` | `arn:aws:iam::<dev account>:role/groundskeeper-ci-deploy` |
| `AWS_PROD_DEPLOY_ROLE_ARN` | `arn:aws:iam::<prod account>:role/groundskeeper-ci-deploy` |
| `AWS_DEV_ACCOUNT_ID` | dev account 12-digit id |
| `AWS_PROD_ACCOUNT_ID` | prod account 12-digit id |
| `GROUNDSKEEPER_DEV_DASHBOARD_PASSWORD` | basic-auth password for dev dashboard (optional — leave unset to preserve current) |
| `GROUNDSKEEPER_PROD_DASHBOARD_PASSWORD` | basic-auth password for prod dashboard (optional — leave unset to preserve current) |

### D) Create the `prod-deploy` environment with required reviewer

The `deploy-prod` job in `main.yml` declares `environment: prod-deploy`. Create that environment and add yourself as a required reviewer so prod deploys gate on an explicit click:

- Settings → Environments → New environment → name: `prod-deploy`
- Required reviewers → add yourself
- (Optional) Deployment branches: restrict to `main`

### E) Confirm

After A–D, push a no-op commit to `dev` and watch the `dev.yml` run go all the way through `deploy-dev`. Then open a PR `dev → main`, merge, approve the `prod-deploy` environment prompt, and watch `main.yml` go through `deploy-prod`. Resources end up with `project=groundskeeper` + `environment=<env>` on both sides.

## GitHub repo prep

- [ ] Public repo created (`gh repo create <name> --public`)
- [ ] Default branch is `main`
- [ ] Branch protection on `main`:
  - [ ] Require pull request review before merging
  - [ ] Require status checks to pass (CI workflow from `.github/workflows/main.yml` — TODO: confirm workflow filename matches what's in repo)
  - [ ] Disallow force pushes
  - [ ] Disallow deletions
- [ ] CodeQL enabled (Settings → Code security and analysis → Code scanning)
- [ ] Dependabot security updates enabled (Settings → Code security and analysis → Dependabot)
- [ ] Dependabot version updates configured via `.github/dependabot.yml` (TODO: confirm whether you want this on day one)
- [ ] Secret scanning + push protection enabled (Settings → Code security and analysis)
- [ ] Issue templates added under `.github/ISSUE_TEMPLATE/` (optional but recommended)
- [ ] README badges link to the public CI URLs (replace any private-staging URLs)
- [ ] Repo description, topics, and homepage URL filled in
- [ ] "About" sidebar: untick "Releases", "Packages", "Deployments" if unused

## Local cleanup

- [ ] No `.DS_Store` committed: `git ls-files | grep -i DS_Store` returns nothing
- [ ] No editor files committed: `git ls-files | grep -E '\.(idea|vscode)/'` returns nothing
- [ ] No `__pycache__` or `*.pyc` committed: `git ls-files | grep -E '__pycache__|\.pyc$'` returns nothing
- [ ] `git status` is clean before tagging the release
- [ ] Tag and push the release:
  - [ ] `git tag -a v0.1.0 -m "v0.1.0 — initial public release"`
  - [ ] `git push origin v0.1.0`
- [ ] Create the GitHub release from the tag: `gh release create v0.1.0 --generate-notes` (edit notes before publishing)

## Post-release watch

Monitor for 24 hours after publishing:

- [ ] CloudWatch Logs for `groundskeeper-prod-orchestrator`, `groundskeeper-prod-executor`, `groundskeeper-prod-api` — no new ERROR-level entries
- [ ] AWS Cost Explorer (tag `project=groundskeeper`) — no unexpected spike
- [ ] GitHub repo: triage any Issues filed within the first 24h
- [ ] GitHub repo: check Security tab for any new CodeQL / Dependabot alerts
- [ ] Confirm the next scheduled 12:00 UTC cron tick fires and lands a commit on the production target repo
