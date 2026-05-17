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
        <h1 className="text-3xl font-semibold tracking-tight text-white">Commit Style</h1>
        <p className="mt-1 text-sm text-slate-400">
          Shape the prompt and edit behavior the executor uses each time it commits.
        </p>
      </header>

      {error && (
        <div className="mb-6 rounded-lg border border-red-900/60 bg-red-950/40 px-4 py-3 text-sm text-red-200">
          {error}
        </div>
      )}

      {loading || !config ? (
        <div className="text-sm text-slate-500">Loading…</div>
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
  const [value, setValue] = useState(style.prompt)
  const [saving, setSaving] = useState(false)
  const [savedAt, setSavedAt] = useState<number | null>(null)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    setValue(style.prompt)
  }, [style.prompt])

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
      setErr(e instanceof Error ? e.message : 'Save failed.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Card>
      <CardHeader title="Creative prompt" icon={<Sparkles size={18} />}>
        <SaveBadge saving={saving} dirty={dirty} savedAt={savedAt} />
      </CardHeader>
      <p className="mb-3 text-sm text-slate-400">
        Sent to Nova Lite alongside each creative commit. Describe the tone, voice, and what the
        model should add — comments, docstrings, helper utilities, whatever fits.
      </p>
      <textarea
        rows={5}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        className="w-full resize-y rounded-md border border-slate-800 bg-slate-900 px-3 py-2 font-mono text-sm leading-relaxed text-slate-100 placeholder:text-slate-600 focus:border-sky-700 focus:ring-1 focus:ring-sky-700 focus:outline-none"
        placeholder="e.g. Write code comments and docstrings in the style of a thoughtful, occasionally tired but passionate developer."
      />
      {err && <p className="mt-2 text-xs text-red-300">{err}</p>}
      <div className="mt-3 flex items-center justify-between gap-2">
        <p className="text-xs text-slate-500">{trimmed.length} characters</p>
        <div className="flex gap-2">
          {dirty && (
            <button
              type="button"
              onClick={() => setValue(style.prompt)}
              disabled={saving}
              className="rounded-md border border-slate-800 px-3 py-1.5 text-xs text-slate-300 transition-colors hover:bg-slate-900 disabled:cursor-not-allowed disabled:text-slate-500"
            >
              Discard
            </button>
          )}
          <button
            type="button"
            onClick={save}
            disabled={!dirty || !valid || saving}
            className="inline-flex items-center gap-1.5 rounded-md bg-sky-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-sky-500 disabled:cursor-not-allowed disabled:bg-slate-700 disabled:text-slate-400"
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
  const [pct, setPct] = useState(() => Math.round(style.destructive_probability * 100))
  const [saving, setSaving] = useState(false)
  const [savedAt, setSavedAt] = useState<number | null>(null)

  useEffect(() => {
    setPct(Math.round(style.destructive_probability * 100))
  }, [style.destructive_probability])

  const saved = Math.round(style.destructive_probability * 100)
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
      <p className="mb-4 text-sm text-slate-400">
        Each fired commit has this chance of being a <em>maintenance</em> commit — the model decides
        whether to remove low-value lines or rewrite an existing section in place, rather than
        adding new content. Falls back to a creative commit if the file is nearly empty.
      </p>
      <div className="mb-2 flex items-baseline justify-between">
        <span className="text-xs tracking-wide text-slate-500 uppercase">Probability</span>
        <span className="text-2xl font-semibold text-sky-300 tabular-nums">{pct}%</span>
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
      <div className="mt-2 flex justify-between text-xs text-slate-500">
        <span>0%</span>
        <span>50%</span>
        <span>100%</span>
      </div>
      <CardFooter>
        At <span className="text-slate-400">{pct}%</span>, roughly {humanFrequency(pct)} of commits
        will be maintenance edits.
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
  const [minText, setMinText] = useState(String(style.min_lines))
  const [maxText, setMaxText] = useState(String(style.max_lines))
  const [saving, setSaving] = useState(false)
  const [savedAt, setSavedAt] = useState<number | null>(null)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    setMinText(String(style.min_lines))
    setMaxText(String(style.max_lines))
  }, [style.min_lines, style.max_lines])

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
      setErr(e instanceof Error ? e.message : 'Save failed.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Card>
      <CardHeader title="Lines added per commit" icon={<FileText size={18} />}>
        <SaveBadge saving={saving} dirty={dirty} savedAt={savedAt} />
      </CardHeader>
      <p className="mb-4 text-sm text-slate-400">
        On a creative commit the model adds somewhere in this range. Small ranges read as steady
        activity; wide ranges look like sweeping refactors. Up to {LINE_CAP} lines.
      </p>
      <div className="grid grid-cols-2 gap-4">
        <NumberInput label="Minimum" value={minText} onChange={setMinText} placeholder="1" />
        <NumberInput label="Maximum" value={maxText} onChange={setMaxText} placeholder="8" />
      </div>
      {willClamp && (
        <p className="mt-3 text-xs text-slate-500">
          Saves as <span className="text-slate-300 tabular-nums">{cMin}</span>–
          <span className="text-slate-300 tabular-nums">{cMax}</span> (clamped: min ≥ 1, max ≥ min,
          ≤ {LINE_CAP}).
        </p>
      )}
      {err && <p className="mt-3 text-xs text-red-300">{err}</p>}
      <div className="mt-4 flex justify-end gap-2">
        {dirty && (
          <button
            type="button"
            onClick={() => {
              setMinText(String(style.min_lines))
              setMaxText(String(style.max_lines))
            }}
            disabled={saving}
            className="rounded-md border border-slate-800 px-3 py-1.5 text-xs text-slate-300 transition-colors hover:bg-slate-900 disabled:cursor-not-allowed disabled:text-slate-500"
          >
            Discard
          </button>
        )}
        <button
          type="button"
          onClick={save}
          disabled={!dirty || saving}
          className="inline-flex items-center gap-1.5 rounded-md bg-sky-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-sky-500 disabled:cursor-not-allowed disabled:bg-slate-700 disabled:text-slate-400"
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
  if (saving) {
    return (
      <span className="inline-flex items-center gap-1.5 text-xs text-slate-500">
        <Loader size={12} className="animate-spin" /> Saving…
      </span>
    )
  }
  if (dirty) {
    return <span className={cn('text-xs text-amber-300')}>Unsaved</span>
  }
  if (savedAt && Date.now() - savedAt < 2_500) {
    return (
      <span className="inline-flex items-center gap-1.5 text-xs text-emerald-400">
        <Check size={12} /> Saved
      </span>
    )
  }
  return null
}
