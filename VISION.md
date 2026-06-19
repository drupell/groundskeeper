# Vision

## The shape of the project

Groundskeeper is a framework for handing an LLM a repository plus a rough
direction, then watching what it actually does with it. You provide a repo
you own, write a short style prompt, and shape a per-day window and
commit-count curve. Once a day, an agent picks an eligible file, decides
between a creative edit (additive content that matches your prompt) or a
destructive edit (removing or restructuring a small section), and commits
the result. You get to read it the next morning.

What accumulates isn't a body of code — it's a corpus of decisions. Which
files did the agent keep reaching for? Which did it leave alone? What tone
did it settle into when you only gave it a sentence to go on? When you
nudged the destructive-edit probability up, what did it choose to remove?
Over weeks, the answers start to look like a behavioral fingerprint of
that particular model against that particular repo and that particular
prompt.

The contribution graph fills in as a side effect of how the executor
schedules itself. That is not the point. The point is the observation
loop: you set the lever, the agent makes its daily decision, the log
remembers what it did, and you can go back and ask why.

## What v0.1.0 ships

- A single repo per deployment, configured from the dashboard.
- Amazon Bedrock Nova Lite as the agent, called once per scheduled commit.
- A fixed prompt structure: creative vs destructive, chosen per commit
  against a probability you set.
- A dashboard for shaping the daily window, sculpting the commit-count
  curve, writing the style prompt, and watching the commit log fill in.
- Single user, single AWS account, one-command deploy and one-command
  teardown.

## What's deferred (the arc)

These are possible directions, not commitments. v0.1.0 is the smallest
honest version of the observation loop; everything below makes that loop
richer.

- **Multi-repo per deployment.** Watch the same agent against several
  repos in parallel, with their own prompts and schedules, and compare.
- **Pluggable model providers and agents.** Swap Nova Lite for a larger
  Bedrock model, an Anthropic model, or a local model, and see how the
  decisions change against the same prompt.
- **Richer prompt scaffolds.** The fixed creative-vs-destructive split is
  the simplest possible structure. Plan-then-execute, research-then-edit,
  or multi-step deliberation would let you observe more interesting
  decision shapes.
- **Decision telemetry.** Beyond a flat commit log: which files keep
  coming up, which get left alone, what voice the model settles into,
  how often it picks destructive when given the freedom.
- **Cross-model comparison.** Run the same prompt and repo against two
  agents on alternating days, and surface the differences as a first-class
  view.
- **Per-run observability.** Capture not just the commit but the agent's
  intermediate state — what it considered, what it rejected — when the
  scaffolds get richer.

## What this isn't

- **Not a way to game the contribution graph.** The graph filling up is a
  side effect; if that's what you want, there are simpler tools, and
  you'll get more honest results from them.
- **Not a code-writing tool you'd use in production.** Nova Lite is making
  small, often whimsical edits to a repo you've designated as the
  observation surface. Don't point this at anything you ship.
- **Not a content generator.** The prompt shapes the agent's voice, but
  the artifact you keep is the log of decisions, not the prose.
- **Not multi-tenant.** One user, one dashboard, one repo per deployment.
  PRs toward a hosted service aren't a fit.

## How v0.1.0 fits the arc

The executor, orchestrator, and DynamoDB commit log already form the
observation primitive: an agent makes a decision, the system records what
it chose and why it was eligible, and the record survives. The dashboard,
curve editor, and style prompt are the user's levers on that primitive —
how often the agent gets to act, inside what window, with what direction.
Everything in the deferred arc above is "make these primitives richer":
more agents to observe, more prompt structures to give them, more
dimensions along which to read the resulting corpus.
