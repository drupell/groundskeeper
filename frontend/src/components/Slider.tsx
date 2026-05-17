// Thin wrapper over a native <input type="range"> styled by the `.gk-slider`
// class in `index.css`. Native gives us free keyboard support and proper
// touch/pointer behavior; the wrapper just normalizes onChange to a number
// and exposes an onCommit callback that fires on pointer-up / change-end.

import type { ChangeEvent } from 'react'

import { cn } from '@/lib/utils'

interface SliderProps {
  value: number
  onChange: (v: number) => void
  onCommit?: (v: number) => void
  min: number
  max: number
  step?: number
  ariaLabel?: string
  className?: string
}

export function Slider({
  value,
  onChange,
  onCommit,
  min,
  max,
  step = 1,
  ariaLabel,
  className,
}: SliderProps) {
  const handleChange = (e: ChangeEvent<HTMLInputElement>) => onChange(Number(e.target.value))
  return (
    <input
      type="range"
      value={value}
      onChange={handleChange}
      onPointerUp={() => onCommit?.(value)}
      onKeyUp={(e) => {
        // Commit when keyboard arrows release. Saves a server round-trip
        // for every keypress while still feeling instant.
        if (
          onCommit &&
          (e.key === 'ArrowLeft' ||
            e.key === 'ArrowRight' ||
            e.key === 'ArrowUp' ||
            e.key === 'ArrowDown' ||
            e.key === 'Home' ||
            e.key === 'End')
        ) {
          onCommit(value)
        }
      }}
      min={min}
      max={max}
      step={step}
      aria-label={ariaLabel}
      className={cn('gk-slider', className)}
    />
  )
}
