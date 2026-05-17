import { Outlet } from 'react-router-dom'

import { Sidebar } from '@/components/Sidebar'

export function Layout() {
  return (
    <div className="flex min-h-screen bg-slate-900 text-slate-100">
      <Sidebar />
      <main className="flex-1 overflow-auto">
        <Outlet />
      </main>
    </div>
  )
}
