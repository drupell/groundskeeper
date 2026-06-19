import { Monitor, Moon, Sun } from 'lucide-react'

import { useTheme, type Theme } from '@/lib/useTheme'

const NEXT: Record<Theme, Theme> = {
  light: 'dark',
  dark: 'system',
  system: 'light',
}

const LABEL: Record<Theme, string> = {
  light: 'Switch to dark mode',
  dark: 'Switch to system theme',
  system: 'Switch to light mode',
}

export function ThemeToggle() {
  const { theme, setTheme } = useTheme()
  const Icon = theme === 'light' ? Sun : theme === 'dark' ? Moon : Monitor

  return (
    <button
      type="button"
      onClick={() => setTheme(NEXT[theme])}
      aria-label={LABEL[theme]}
      title={LABEL[theme]}
      className="inline-flex h-6 w-6 items-center justify-center rounded text-[var(--color-fg-muted)] transition-colors hover:text-[var(--color-fg)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-control-ring-focus)]"
    >
      <Icon size={14} />
    </button>
  )
}
