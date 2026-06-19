# Contributing

## Project scope

Groundskeeper is a single-tenant framework for handing an LLM a repo and a rough direction, then observing the decisions it makes day by day. PRs that sharpen that observation loop — the executor, the orchestrator, the dashboard levers, the commit log — are welcome; refactors toward multi-tenancy, team features, or hosted-service ambitions aren't a fit and won't be merged. See [VISION.md](./VISION.md) for the bigger arc before opening a PR.

## Getting set up

```
git clone <repo>
cd Groundskeeper
uv sync
cd frontend && npm ci
```

## Before opening a PR

- [ ] All checks pass locally (see the "Code + tests" section of [RELEASE_CHECKLIST.md](./RELEASE_CHECKLIST.md))
- [ ] You added a test for any new behavior in `backend/tests/`
- [ ] You ran the affected page in the dashboard locally (`cd frontend && npm run dev`) if it's UI
- [ ] No new dependency added without a one-line rationale in the PR description

## Style

- Python: `ruff format` (or `black`), `ruff check`, 100-col line length
- TypeScript: `prettier` (project config), `eslint`, `tsc --noEmit`
- Comments: WHY-only (project convention — see code style notes)
- Voice for user-facing strings: warm + specific + jargon-light (no internal sigils in user messages)

## Releases

Maintainer-only — see [RELEASE_CHECKLIST.md](./RELEASE_CHECKLIST.md).
