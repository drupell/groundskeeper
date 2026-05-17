// A digits-only text input that allows free editing (including empty/
// intermediate states) while typing. It deliberately does NOT clamp on
// change — callers clamp once, on save. This avoids the "clear the box and
// it snaps to 1" annoyance.

interface NumberInputProps {
  label: string
  value: string
  onChange: (raw: string) => void
  placeholder?: string
}

export function NumberInput({ label, value, onChange, placeholder }: NumberInputProps) {
  return (
    <label className="block">
      <span className="text-xs tracking-wide text-slate-500 uppercase">{label}</span>
      <input
        type="text"
        inputMode="numeric"
        pattern="[0-9]*"
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value.replace(/[^0-9]/g, ''))}
        className="mt-1 w-full rounded-md border border-slate-800 bg-slate-900 px-3 py-2 font-mono text-lg text-slate-100 tabular-nums placeholder:text-slate-600 focus:border-sky-700 focus:ring-1 focus:ring-sky-700 focus:outline-none"
      />
    </label>
  )
}
