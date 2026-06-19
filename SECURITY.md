# Security Policy

Thanks for helping keep Groundskeeper and its users safe.

## Supported versions

Groundskeeper is a small, fast-moving project. We patch security issues on:

- `main` (the active development branch)
- the latest tagged release

Older tags don't receive backports. If you're on one, upgrading is the fix.

## Reporting a vulnerability

Please **do not** open a public issue for security problems.

The preferred path is a private security advisory on GitHub:
<https://docs.github.com/en/code-security/security-advisories/repository-security-advisories/about-repository-security-advisories>

If that's not an option, email the maintainer listed in `pyproject.toml`.

Include: a description, repro steps, the impact you see, and any suggested fix.
We'll acknowledge within a few business days and keep you in the loop while we
work it.

## Disclosure window

Standard 90 days from acknowledgement to public disclosure. We may ship a fix
sooner; we may ask for an extension if the fix is genuinely tricky. Either way,
we'll coordinate with you before disclosing.

## Out of scope

Groundskeeper is single-tenant by design — you deploy it into your own AWS
account and you are the only user. As a result:

- There is no authentication or multi-user authorization layer to attack.
- Compromise of the AWS account hosting Groundskeeper is the operator's
  responsibility (IAM hygiene, root account MFA, credential rotation, etc.).
- Reports that boil down to "someone with admin in your AWS account could do
  bad things" are out of scope.

Vulnerabilities in the deployed Lambda / API Gateway / DynamoDB code path, the
deploy/teardown scripts, or the dashboard itself are very much in scope.
