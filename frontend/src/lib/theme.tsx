import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'

export type Theme = 'light' | 'dark' | 'system'
export type EffectiveTheme = 'light' | 'dark'

const STORAGE_KEY = 'gk-theme'

interface ThemeContextValue {
  theme: Theme
  setTheme: (theme: Theme) => void
  effective: EffectiveTheme
}

const ThemeContext = createContext<ThemeContextValue | undefined>(undefined)

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

function resolveEffective(theme: Theme): EffectiveTheme {
  if (theme === 'system') {
    return systemPrefersDark() ? 'dark' : 'light'
  }
  return theme
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<Theme>(() => readStoredTheme())
  const [effective, setEffective] = useState<EffectiveTheme>(() =>
    resolveEffective(readStoredTheme()),
  )

  // Persist + resolve whenever the chosen theme changes.
  useEffect(() => {
    try {
      window.localStorage.setItem(STORAGE_KEY, theme)
    } catch {
      // Ignore — storage might be unavailable.
    }
    setEffective(resolveEffective(theme))
  }, [theme])

  // When following the system, listen for OS-level changes.
  useEffect(() => {
    if (theme !== 'system') return
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return

    const media = window.matchMedia('(prefers-color-scheme: dark)')
    const handler = (event: MediaQueryListEvent) => {
      setEffective(event.matches ? 'dark' : 'light')
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

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext)
  if (!ctx) {
    throw new Error('useTheme must be used inside a <ThemeProvider>')
  }
  return ctx
}
