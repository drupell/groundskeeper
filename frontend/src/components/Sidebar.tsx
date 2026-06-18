import { LayoutDashboard, ScrollText, Settings, Sparkles, TrendingUp } from 'lucide-react'

import { GithubMark } from '@/components/icons'
import { ThemeToggle } from '@/components/ThemeToggle'
import type { ReactNode } from 'react'
import { NavLink } from 'react-router-dom'

import { cn } from '@/lib/utils'

interface NavItem {
  to: string
  label: string
  icon: ReactNode
  end?: boolean
}

const NAV: NavItem[] = [
  { to: '/', label: 'Dashboard', icon: <LayoutDashboard size={18} />, end: true },
  { to: '/schedule', label: 'Schedule', icon: <TrendingUp size={18} /> },
  { to: '/commit-style', label: 'Commit Style', icon: <Sparkles size={18} /> },
  { to: '/github', label: 'GitHub', icon: <GithubMark size={18} /> },
  { to: '/logs', label: 'Logs', icon: <ScrollText size={18} /> },
  { to: '/settings', label: 'Settings', icon: <Settings size={18} /> },
]

export function Sidebar() {
  return (
    <aside className="flex w-64 shrink-0 flex-col border-r border-[var(--color-border)] bg-[var(--color-surface-elevated)]">
      <div className="border-b border-[var(--color-border)] px-6 py-5">
        <h1 className="text-lg font-semibold tracking-tight text-[var(--color-fg)]">
          Groundskeeper
        </h1>
        <p className="mt-0.5 text-xs text-[var(--color-fg-muted)]">
          Your personal commit autopilot
        </p>
      </div>
      <nav className="flex-1 space-y-1 p-3">
        {NAV.map((item) => (
          <SideLink key={item.to} to={item.to} label={item.label} icon={item.icon} end={item.end} />
        ))}
      </nav>
      <div className="flex items-center gap-2 border-t border-[var(--color-border)] px-4 py-3 text-xs text-[var(--color-fg-muted)]">
        <span>v0.1 · dev preview</span>
        <ThemeToggle />
      </div>
    </aside>
  )
}

function SideLink({
  to,
  label,
  icon,
  end,
}: {
  to: string
  label: string
  icon: ReactNode
  end?: boolean
}) {
  return (
    <NavLink
      to={to}
      end={end}
      className={({ isActive }) =>
        cn(
          'flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors',
          isActive
            ? 'bg-[var(--color-surface-sunk)] text-[var(--color-fg)]'
            : 'text-[var(--color-fg-muted)] hover:bg-[var(--color-surface-sunk)]/60 hover:text-[var(--color-fg)]',
        )
      }
    >
      <span className="text-[var(--color-fg-muted)] transition-colors">{icon}</span>
      <span>{label}</span>
    </NavLink>
  )
}
