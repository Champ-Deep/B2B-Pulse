import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import api from '../api/client'
import { SectionLoading } from '../components/Loading'
import { useAuth } from '../lib/auth'
import type { AnalyticsSummary, IntegrationStatus } from '../lib/types'

export default function Dashboard() {
  const { user } = useAuth()
  const [analytics, setAnalytics] = useState<AnalyticsSummary | null>(null)
  const [integrations, setIntegrations] = useState<IntegrationStatus | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const fetchData = async () => {
      try {
        const [analyticsRes, integrationsRes] = await Promise.all([
          api.get('/audit/analytics/summary'),
          api.get('/integrations/status'),
        ])
        setAnalytics(analyticsRes.data)
        setIntegrations(integrationsRes.data)
      } catch (err) {
        console.error('Failed to load dashboard data:', err)
      } finally {
        setLoading(false)
      }
    }
    fetchData()
  }, [])

  const totalLikes = analytics ? Object.values(analytics.likes).reduce((a, b) => a + b, 0) : 0
  const totalComments = analytics ? Object.values(analytics.comments).reduce((a, b) => a + b, 0) : 0
  const completedActions = (analytics?.likes?.completed || 0) + (analytics?.comments?.completed || 0)
  const pendingActions = (analytics?.likes?.pending || 0) + (analytics?.comments?.pending || 0)

  if (loading) {
    return <SectionLoading />
  }

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Welcome back, {user?.full_name}</h1>
        <p className="text-gray-500 mt-1">Here&apos;s what&apos;s happening with your social engagement</p>
      </div>

      {/* Stats Grid */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-6">
        <StatCard title="Total Likes" value={totalLikes} color="blue" />
        <StatCard title="Total Comments" value={totalComments} color="green" />
        <StatCard title="Completed" value={completedActions} color="emerald" />
        <StatCard title="Pending" value={pendingActions} color="yellow" />
      </div>

      {/* Integration Status */}
      <div className="bg-white rounded-xl shadow-sm p-6">
        <h2 className="text-lg font-semibold mb-4">Integration Status</h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <IntegrationCard
            name="LinkedIn"
            connected={integrations?.linkedin.connected || false}
            active={integrations?.linkedin.active || false}
          />
          <IntegrationCard
            name="Meta (Instagram/Facebook)"
            connected={integrations?.meta.connected || false}
            active={integrations?.meta.active || false}
          />
          <IntegrationCard
            name="WhatsApp"
            connected={integrations?.whatsapp.connected || false}
            active={integrations?.whatsapp.active || false}
          />
        </div>
      </div>

      {/* Quick Actions */}
      <div className="bg-white rounded-xl shadow-sm p-6">
        <h2 className="text-lg font-semibold mb-4">Quick Actions</h2>
        <div className="flex flex-wrap gap-3">
          <Link to="/tracked-pages" className="px-4 py-2 bg-primary-50 text-primary-700 rounded-lg text-sm font-medium hover:bg-primary-100 transition-colors">
            Add Tracked Pages
          </Link>
          <Link to="/onboarding" className="px-4 py-2 bg-primary-50 text-primary-700 rounded-lg text-sm font-medium hover:bg-primary-100 transition-colors">
            Update Profile & Style
          </Link>
          <Link to="/settings" className="px-4 py-2 bg-primary-50 text-primary-700 rounded-lg text-sm font-medium hover:bg-primary-100 transition-colors">
            Connect LinkedIn
          </Link>
          <Link to="/audit" className="px-4 py-2 bg-primary-50 text-primary-700 rounded-lg text-sm font-medium hover:bg-primary-100 transition-colors">
            View Audit Log
          </Link>
        </div>
      </div>
    </div>
  )
}

function StatCard({ title, value, color }: { title: string; value: number; color: string }) {
  const colorMap: Record<string, string> = {
    blue: 'bg-blue-50 text-blue-700',
    green: 'bg-green-50 text-green-700',
    emerald: 'bg-emerald-50 text-emerald-700',
    yellow: 'bg-yellow-50 text-yellow-700',
  }
  return (
    <div className={`rounded-xl p-6 ${colorMap[color] || 'bg-gray-50 text-gray-700'}`}>
      <p className="text-sm font-medium opacity-75">{title}</p>
      <p className="text-3xl font-bold mt-1">{value}</p>
    </div>
  )
}

function IntegrationCard({ name, connected, active }: { name: string; connected: boolean; active: boolean }) {
  return (
    <div className="flex items-center justify-between p-4 border rounded-lg">
      <span className="font-medium text-sm">{name}</span>
      <span className={`px-2.5 py-1 rounded-full text-xs font-medium ${
        connected && active
          ? 'bg-green-100 text-green-700'
          : connected
            ? 'bg-yellow-100 text-yellow-700'
            : 'bg-gray-100 text-gray-500'
      }`}>
        {connected && active ? 'Active' : connected ? 'Connected' : 'Not connected'}
      </span>
    </div>
  )
}
