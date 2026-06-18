// Accessible on/off toggle. role="switch" + aria-checked so screen readers
// announce it correctly; keyboard works for free since it's a <button>.

import { cn } from '@/lib/utils'

interface SwitchProps {
  checked: boolean
  onChange: (next: boolean) => void
  disabled?: boolean
  ariaLabel?: string
}

export function Switch({ checked, onChange, disabled, ariaLabel }: SwitchProps) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={ariaLabel}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cn(
        'relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-brand)]/50',
        checked ? 'bg-[var(--color-ink)]' : 'bg-[var(--color-surface-sunk)]',
        disabled && 'cursor-not-allowed opacity-50',
      )}
    >
      <span
        className={cn(
          'inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform',
          checked ? 'translate-x-[18px]' : 'translate-x-0.5',
        )}
      />
    </button>
  )
}
