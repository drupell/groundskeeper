import { Outlet } from 'react-router-dom'

import { Sidebar } from '@/components/Sidebar'

export function Layout() {
  return (
    <div className="flex min-h-screen bg-[var(--color-surface)] text-[var(--color-fg)]">
      <Sidebar />
      <main className="flex-1 overflow-auto">
        <Outlet />
      </main>
    </div>
  )
}
