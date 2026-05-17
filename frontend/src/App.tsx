import { BrowserRouter, Route, Routes } from 'react-router-dom'

import { Layout } from '@/components/Layout'
import { CommitStylePage } from '@/pages/CommitStyle'
import { DashboardPage } from '@/pages/Dashboard'
import { GitHubPage } from '@/pages/GitHub'
import { LogsPage } from '@/pages/Logs'
import { SchedulePage } from '@/pages/Schedule'
import { SettingsPage } from '@/pages/Settings'

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<DashboardPage />} />
          <Route path="schedule" element={<SchedulePage />} />
          <Route path="commit-style" element={<CommitStylePage />} />
          <Route path="github" element={<GitHubPage />} />
          <Route path="logs" element={<LogsPage />} />
          <Route path="settings" element={<SettingsPage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}

export default App
