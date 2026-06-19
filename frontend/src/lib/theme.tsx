import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'

import {
  ThemeContext,
  type EffectiveTheme,
  type Theme,
  type ThemeContextValue,
} from '@/lib/useTheme'

const STORAGE_KEY = 'gk-theme'

function readStoredTheme(): Theme {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (raw === 'light' || raw === 'dark' || raw === 'system') {
      return raw
    }
  } catch {
    // localStorage may be unavailable (private mode, SSR, etc.) — fall through.
  }
  return 'system'
}

function systemPrefersDark(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
    return false
  }
  return window.matchMedia('(prefers-color-scheme: dark)').matches
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<Theme>(() => readStoredTheme())
  const [systemDark, setSystemDark] = useState<boolean>(() => systemPrefersDark())

  // Derived state: the effective theme is a pure function of the chosen theme
  // and the current system preference, so we don't store it separately.
  const effective = useMemo<EffectiveTheme>(() => {
    if (theme === 'system') {
      return systemDark ? 'dark' : 'light'
    }
    return theme
  }, [theme, systemDark])

  // Persist the chosen theme whenever it changes.
  useEffect(() => {
    try {
      window.localStorage.setItem(STORAGE_KEY, theme)
    } catch {
      // Ignore — storage might be unavailable.
    }
  }, [theme])

  // When following the system, listen for OS-level changes.
  useEffect(() => {
    if (theme !== 'system') return
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return

    const media = window.matchMedia('(prefers-color-scheme: dark)')
    const handler = (event: MediaQueryListEvent) => {
      setSystemDark(event.matches)
    }

    // Older Safari uses addListener/removeListener.
    if (typeof media.addEventListener === 'function') {
      media.addEventListener('change', handler)
      return () => media.removeEventListener('change', handler)
    }
    media.addListener(handler)
    return () => media.removeListener(handler)
  }, [theme])

  // Apply the .dark class on <html> whenever the effective theme changes.
  useEffect(() => {
    const root = document.documentElement
    if (effective === 'dark') {
      root.classList.add('dark')
    } else {
      root.classList.remove('dark')
    }
  }, [effective])

  const setTheme = useCallback((next: Theme) => {
    setThemeState(next)
  }, [])

  const value = useMemo<ThemeContextValue>(
    () => ({ theme, setTheme, effective }),
    [theme, setTheme, effective],
  )

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
}
