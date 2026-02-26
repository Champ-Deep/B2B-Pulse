import { useCallback, useEffect, useState } from 'react'
import { Route, Routes, Navigate } from 'react-router-dom'
import api from './api/client'
import { AuthContext } from './lib/auth'
import { ROUTES } from './lib/routes'
import type { User } from './lib/types'
import ErrorBoundary from './components/ErrorBoundary'
import { ToastProvider } from './components/Toast'
import Layout from './components/Layout'
import Login from './pages/Login'
import Signup from './pages/Signup'
import AuthCallback from './pages/AuthCallback'
import Dashboard from './pages/Dashboard'
import Onboarding from './pages/Onboarding'
import TrackedPages from './pages/TrackedPages'
import AutomationSettings from './pages/AutomationSettings'
import AuditLog from './pages/AuditLog'
import Team from './pages/Team'
import AdminDashboard from './pages/AdminDashboard'
import B2BPulseProduct from './pages/B2BPulseProduct'

function App() {
  const [user, setUser] = useState<User | null>(null)
  const [isLoading, setIsLoading] = useState(true)

  const fetchUser = useCallback(async () => {
    const token = localStorage.getItem('access_token')
    if (!token) {
      setIsLoading(false)
      return
    }
    try {
      const { data } = await api.get('/auth/me')
      setUser(data)
    } catch {
      localStorage.removeItem('access_token')
      localStorage.removeItem('refresh_token')
    } finally {
      setIsLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchUser()
  }, [fetchUser])

  const loginWithLinkedIn = async (inviteCode?: string) => {
    const params = inviteCode ? `?invite_code=${inviteCode}` : ''
    const { data } = await api.get(`/auth/linkedin${params}`)
    window.location.href = data.auth_url
  }

  const logout = () => {
    localStorage.removeItem('access_token')
    localStorage.removeItem('refresh_token')
    setUser(null)
  }

  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary-600"></div>
      </div>
    )
  }

  return (
    <ErrorBoundary>
      <ToastProvider>
        <AuthContext.Provider value={{ user, isLoading, loginWithLinkedIn, logout }}>
          <Routes>
            <Route path={ROUTES.AUTH_CALLBACK} element={<AuthCallback />} />
            <Route path={ROUTES.LOGIN} element={user ? <Navigate to={ROUTES.HOME} /> : <Login />} />
            <Route path={ROUTES.SIGNUP} element={user ? <Navigate to={ROUTES.HOME} /> : <Signup />} />
            <Route path={ROUTES.PRODUCT} element={<B2BPulseProduct />} />
            <Route element={user ? <Layout /> : <Navigate to={ROUTES.LOGIN} />}>
              <Route path={ROUTES.HOME} element={<Dashboard />} />
              <Route path={ROUTES.ONBOARDING} element={<Onboarding />} />
              <Route path={ROUTES.TRACKED_PAGES} element={<TrackedPages />} />
              <Route path={ROUTES.SETTINGS} element={<AutomationSettings />} />
              <Route path={ROUTES.TEAM} element={<Team />} />
              <Route path={ROUTES.AUDIT} element={<AuditLog />} />
              {user?.is_platform_admin && (
                <Route path={ROUTES.ADMIN} element={<AdminDashboard />} />
              )}
            </Route>
          </Routes>
        </AuthContext.Provider>
      </ToastProvider>
    </ErrorBoundary>
  )
}

export default App
