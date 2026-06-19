// Commit Style — the LLM-side configuration.
//
// Three independently-saveable cards: the creative system prompt, the
// destructive-commit probability, and the per-commit line-count range.
// Each card mirrors the relevant slice of `config.commit_style` and PATCHes
// only its own subset, so saves don't clobber each other.

import { Check, FileText, Loader, Sparkles, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'

import { NumberInput } from '@/components/NumberInput'
import { Slider } from '@/components/Slider'
import { Card, CardFooter, CardHeader } from '@/components/ui/Card'
import { useConfig } from '@/hooks/useApi'
import type { CommitStyleConfig } from '@/lib/api'
import { cn } from '@/lib/utils'

export function CommitStylePage() {
  const { config, loading, error } = useConfig()

  return (
    <div className="px-8 py-10">
      <header className="mb-8">
        <h1 className="text-3xl font-semibold tracking-tight text-[var(--color-fg)]">
          Commit Style
        </h1>
        <p className="mt-1 text-sm text-[var(--color-fg-muted)]">
          Shape the prompt and the editing behavior we use each time we commit.
        </p>
      </header>

      {error && (
        <div className="mb-6 rounded-lg border border-rose-900/60 bg-rose-950/40 px-4 py-3 text-sm text-rose-200">
          {error}
        </div>
      )}

      {loading || !config ? (
        <div className="text-sm text-[var(--color-fg-muted)]">One moment…</div>
      ) : (
        <div className="max-w-2xl space-y-6">
          <PromptCard style={config.commit_style} />
          <DestructiveCard style={config.commit_style} />
          <LineRangeCard style={config.commit_style} />
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Prompt — long-form textarea
// ---------------------------------------------------------------------------

function PromptCard({ style }: { style: CommitStyleConfig }) {
  const { updateConfig } = useConfig()
  // Pattern A3: track which saved prompt our local edit branched from, and
  // reset during render when the server value changes. This preserves in-flight
  // edits across re-renders without a sync useEffect.
  const [valueState, setValueState] = useState({ key: style.prompt, value: style.prompt })
  if (valueState.key !== style.prompt) {
    setValueState({ key: style.prompt, value: style.prompt })
  }
  const value = valueState.key === style.prompt ? valueState.value : style.prompt
  const setValue = (next: string) => setValueState({ key: style.prompt, value: next })
  const [saving, setSaving] = useState(false)
  const [savedAt, setSavedAt] = useState<number | null>(null)
  const [err, setErr] = useState<string | null>(null)

  const trimmed = value.trim()
  const dirty = trimmed !== style.prompt
  const valid = trimmed.length > 0

  const save = async () => {
    if (!dirty || !valid) return
    setSaving(true)
    setErr(null)
    try {
      await updateConfig({ commit_style: { ...style, prompt: trimmed } })
      setSavedAt(Date.now())
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Couldn't save — give it another go.")
    } finally {
      setSaving(false)
    }
  }

  return (
    <Card>
      <CardHeader title="Creative prompt" icon={<Sparkles size={18} />}>
        <SaveBadge saving={saving} dirty={dirty} savedAt={savedAt} />
      </CardHeader>
      <p className="mb-3 text-sm text-[var(--color-fg-muted)]">
        We send this to the model with every creative commit. Describe the tone, voice, and what it
        should add — comments, docstrings, helper utilities, whatever fits.
      </p>
      <textarea
        rows={5}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        className="w-full resize-y rounded-md border border-[var(--color-border)] bg-[var(--color-surface-sunk)] px-3 py-2 font-mono text-sm leading-relaxed text-[var(--color-fg)] placeholder:text-[var(--color-fg-dim)] focus:border-[var(--color-brand)] focus:ring-1 focus:ring-[var(--color-brand)] focus:outline-none"
        placeholder="e.g. Write code comments and docstrings in the style of a thoughtful, occasionally tired but passionate developer."
      />
      {err && <p className="mt-2 text-xs text-rose-300">{err}</p>}
      <div className="mt-3 flex items-center justify-between gap-2">
        <p className="text-xs text-[var(--color-fg-muted)]">{trimmed.length} characters</p>
        <div className="flex gap-2">
          {dirty && (
            <button
              type="button"
              onClick={() => setValue(style.prompt)}
              disabled={saving}
              className="rounded-md border border-[var(--color-border)] px-3 py-1.5 text-xs text-[var(--color-fg)] transition-colors hover:bg-[var(--color-surface-sunk)] disabled:cursor-not-allowed disabled:text-[var(--color-fg-dim)]"
            >
              Discard
            </button>
          )}
          <button
            type="button"
            onClick={save}
            disabled={!dirty || !valid || saving}
            className="inline-flex items-center gap-1.5 rounded-md bg-[var(--color-ink)] px-3 py-1.5 text-xs font-medium text-[var(--color-ink-fg)] transition-colors hover:bg-[var(--color-ink-hover)] disabled:cursor-not-allowed disabled:bg-[var(--color-border)] disabled:text-[var(--color-fg-dim)]"
          >
            {saving && <Loader size={12} className="animate-spin" />}
            Save
          </button>
        </div>
      </div>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// Destructive probability — slider, 0..100%
// ---------------------------------------------------------------------------

function DestructiveCard({ style }: { style: CommitStyleConfig }) {
  const { updateConfig } = useConfig()
  const saved = Math.round(style.destructive_probability * 100)
  // Pattern A3: keep slider position locally editable but snap it back when the
  // saved probability changes underneath us (e.g. another tab saved). Resetting
  // during render avoids the wasted useEffect render.
  const [pctState, setPctState] = useState({ key: saved, value: saved })
  if (pctState.key !== saved) {
    setPctState({ key: saved, value: saved })
  }
  const pct = pctState.key === saved ? pctState.value : saved
  const setPct = (next: number) => setPctState({ key: saved, value: next })
  const [saving, setSaving] = useState(false)
  const [savedAt, setSavedAt] = useState<number | null>(null)

  const dirty = pct !== saved

  const commit = async (next: number) => {
    if (next === saved) return
    setSaving(true)
    try {
      await updateConfig({
        commit_style: { ...style, destructive_probability: next / 100 },
      })
      setSavedAt(Date.now())
    } finally {
      setSaving(false)
    }
  }

  return (
    <Card>
      <CardHeader title="Destructive commits" icon={<Trash2 size={18} />}>
        <SaveBadge saving={saving} dirty={dirty} savedAt={savedAt} />
      </CardHeader>
      <p className="mb-4 text-sm text-[var(--color-fg-muted)]">
        Every commit has this chance of being a <em>maintenance</em> commit. Instead of adding new
        content, the model trims low-value lines or rewrites a section in place. If the file's too
        thin to edit, we fall back to a creative commit.
      </p>
      <div className="mb-2 flex items-baseline justify-between">
        <span className="text-xs tracking-wide text-[var(--color-fg-muted)] uppercase">
          Probability
        </span>
        <span className="text-2xl font-semibold text-[var(--color-brand)] tabular-nums">
          {pct}%
        </span>
      </div>
      <Slider
        value={pct}
        min={0}
        max={100}
        step={1}
        ariaLabel="Destructive commit probability"
        onChange={setPct}
        onCommit={commit}
      />
      <div className="mt-2 flex justify-between text-xs text-[var(--color-fg-muted)]">
        <span>0%</span>
        <span>50%</span>
        <span>100%</span>
      </div>
      <CardFooter>
        At <span className="text-[var(--color-fg)]">{pct}%</span>, roughly {humanFrequency(pct)} of
        commits will be maintenance edits.
      </CardFooter>
    </Card>
  )
}

function humanFrequency(pct: number): string {
  if (pct === 0) return 'no'
  if (pct === 100) return 'every one'
  if (pct < 10) return `~1 in ${Math.round(100 / pct)}`
  if (pct < 50) return `~${pct} in 100`
  return `${pct} of every 100`
}

// ---------------------------------------------------------------------------
// Lines per commit — two number inputs
// ---------------------------------------------------------------------------

const LINE_CAP = 1000

function LineRangeCard({ style }: { style: CommitStyleConfig }) {
  const { updateConfig } = useConfig()
  // Pattern A3: composite key tracks the saved (min, max) pair we branched
  // from; when either side changes on the server, we reset both inputs during
  // render rather than papering over it with a sync useEffect.
  const savedKey = `${style.min_lines}/${style.max_lines}`
  const [textState, setTextState] = useState({
    key: savedKey,
    min: String(style.min_lines),
    max: String(style.max_lines),
  })
  if (textState.key !== savedKey) {
    setTextState({ key: savedKey, min: String(style.min_lines), max: String(style.max_lines) })
  }
  const minText = textState.key === savedKey ? textState.min : String(style.min_lines)
  const maxText = textState.key === savedKey ? textState.max : String(style.max_lines)
  const setMinText = (next: string) =>
    setTextState((s) => ({ key: savedKey, min: next, max: s.key === savedKey ? s.max : maxText }))
  const setMaxText = (next: string) =>
    setTextState((s) => ({ key: savedKey, min: s.key === savedKey ? s.min : minText, max: next }))
  const [saving, setSaving] = useState(false)
  const [savedAt, setSavedAt] = useState<number | null>(null)
  const [err, setErr] = useState<string | null>(null)

  // Clamp only when computing what we'd save: min ≥ 1, max ≥ min, both ≤ cap.
  const cMin = Math.min(LINE_CAP, Math.max(1, parseInt(minText, 10) || 1))
  const cMax = Math.min(LINE_CAP, Math.max(cMin, parseInt(maxText, 10) || cMin))
  const dirty = cMin !== style.min_lines || cMax !== style.max_lines
  const willClamp = minText !== String(cMin) || maxText !== String(cMax)

  const save = async () => {
    if (!dirty) return
    setSaving(true)
    setErr(null)
    try {
      await updateConfig({ commit_style: { ...style, min_lines: cMin, max_lines: cMax } })
      setMinText(String(cMin))
      setMaxText(String(cMax))
      setSavedAt(Date.now())
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Couldn't save — give it another go.")
    } finally {
      setSaving(false)
    }
  }

  return (
    <Card>
      <CardHeader title="Lines added per commit" icon={<FileText size={18} />}>
        <SaveBadge saving={saving} dirty={dirty} savedAt={savedAt} />
      </CardHeader>
      <p className="mb-4 text-sm text-[var(--color-fg-muted)]">
        On a creative commit, the model adds somewhere in this range. Small ranges read as steady
        activity; wide ranges look more like sweeping refactors. Up to {LINE_CAP} lines.
      </p>
      <div className="grid grid-cols-2 gap-4">
        <NumberInput label="Minimum" value={minText} onChange={setMinText} placeholder="1" />
        <NumberInput label="Maximum" value={maxText} onChange={setMaxText} placeholder="8" />
      </div>
      {willClamp && (
        <p className="mt-3 text-xs text-[var(--color-fg-muted)]">
          We'll save this as <span className="text-[var(--color-fg)] tabular-nums">{cMin}</span>–
          <span className="text-[var(--color-fg)] tabular-nums">{cMax}</span> (min ≥ 1, max ≥ min,
          both ≤ {LINE_CAP}).
        </p>
      )}
      {err && <p className="mt-3 text-xs text-rose-300">{err}</p>}
      <div className="mt-4 flex justify-end gap-2">
        {dirty && (
          <button
            type="button"
            onClick={() => {
              setMinText(String(style.min_lines))
              setMaxText(String(style.max_lines))
            }}
            disabled={saving}
            className="rounded-md border border-[var(--color-border)] px-3 py-1.5 text-xs text-[var(--color-fg)] transition-colors hover:bg-[var(--color-surface-sunk)] disabled:cursor-not-allowed disabled:text-[var(--color-fg-dim)]"
          >
            Discard
          </button>
        )}
        <button
          type="button"
          onClick={save}
          disabled={!dirty || saving}
          className="inline-flex items-center gap-1.5 rounded-md bg-[var(--color-ink)] px-3 py-1.5 text-xs font-medium text-[var(--color-ink-fg)] transition-colors hover:bg-[var(--color-ink-hover)] disabled:cursor-not-allowed disabled:bg-[var(--color-border)] disabled:text-[var(--color-fg-dim)]"
        >
          {saving && <Loader size={12} className="animate-spin" />}
          Save
        </button>
      </div>
    </Card>
  )
}

function SaveBadge({
  saving,
  dirty,
  savedAt,
}: {
  saving: boolean
  dirty: boolean
  savedAt: number | null
}) {
  // Pattern B: replace the impure `Date.now() - savedAt < 2_500` check in
  // render with a timer-driven boolean that flips on save and back off after
  // the window. Effect responds to the external savedAt event, so the
  // synchronous setShowSaved call is allowed by set-state-in-effect.
  const [showSaved, setShowSaved] = useState(false)
  useEffect(() => {
    if (!savedAt) return
    // Responding to an external event (savedAt timestamp changing); flipping
    // the boolean synchronously is Pattern B for replacing the impure Date.now()
    // check, but the rule still flags the sync call so we suppress it here.
    // eslint-disable-next-line react-hooks/set-state-in-effect -- Pattern B: timer-driven badge responding to savedAt prop change
    setShowSaved(true)
    const t = window.setTimeout(() => setShowSaved(false), 2_500)
    return () => window.clearTimeout(t)
  }, [savedAt])

  if (saving) {
    return (
      <span className="inline-flex items-center gap-1.5 text-xs text-[var(--color-fg-muted)]">
        <Loader size={12} className="animate-spin" /> Saving…
      </span>
    )
  }
  if (dirty) {
    return <span className={cn('text-xs text-[var(--color-brand)]')}>Unsaved</span>
  }
  if (showSaved) {
    return (
      <span className="inline-flex items-center gap-1.5 text-xs text-emerald-400">
        <Check size={12} /> Saved
      </span>
    )
  }
  return null
}
