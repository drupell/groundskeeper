# GitHub OIDC deploy role

Two ways to wire the per-account CI deploy role that lets GitHub Actions
assume into AWS via OIDC (no long-lived access keys). Pick one — they end up
with the same role.

## 1. Recommended — `deploy.py --bootstrap-oidc`

Once per AWS account, with credentials for that account loaded:

```sh
# Dev account (trusts refs/heads/dev):
uv run python deploy.py --bootstrap-oidc <owner>/<repo> --environment dev

# Prod account (trusts refs/heads/main):
uv run python deploy.py --bootstrap-oidc <owner>/<repo> --environment prod
```

The script is idempotent — it creates the OIDC provider (or reuses the
existing one), creates / updates the `groundskeeper-ci-deploy` role with the
right trust policy, attaches `AdministratorAccess`, and prints the role ARN
to paste into the matching GitHub secret (`AWS_DEV_DEPLOY_ROLE_ARN` or
`AWS_PROD_DEPLOY_ROLE_ARN`). Pass `--yes` to skip the confirmation prompt,
`--role-name` to override the default role name.

## 2. Hand-rolled — templates in this directory

`trust-policy-dev.json` and `trust-policy-main.json` are ready-to-edit
copies. Substitute `<ACCOUNT_ID>`, `<GITHUB_USER>`, and `<REPO_NAME>` (the
branch is already baked into each file's `sub`), then:

```sh
aws iam create-open-id-connect-provider \
  --url https://token.actions.githubusercontent.com \
  --client-id-list sts.amazonaws.com \
  --thumbprint-list 6938fd4d98bab03faadb97b34396831e3780aea1

aws iam create-role --role-name groundskeeper-ci-deploy \
  --assume-role-policy-document file://trust-policy-dev.json   # or -main.json

aws iam attach-role-policy --role-name groundskeeper-ci-deploy \
  --policy-arn arn:aws:iam::aws:policy/AdministratorAccess
```

See [`../../RELEASE_CHECKLIST.md`](../../RELEASE_CHECKLIST.md) →
"CI deploy bootstrap (one-time per AWS account)" for the full walk-through,
including the GitHub repo secret names and the `prod-deploy` required-
reviewer environment.
