import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import api from '../api/client'
import { SectionLoading } from '../components/Loading'
import { useAuth } from '../lib/auth'
import type { ActivityFeedItem, AnalyticsSummary, IntegrationStatus } from '../lib/types'

export default function Dashboard() {
  const { user } = useAuth()
  const [analytics, setAnalytics] = useState<AnalyticsSummary | null>(null)
  const [integrations, setIntegrations] = useState<IntegrationStatus | null>(null)
  const [activity, setActivity] = useState<ActivityFeedItem[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const fetchData = async () => {
      try {
        const [analyticsRes, integrationsRes, activityRes] = await Promise.all([
          api.get('/audit/analytics/summary'),
          api.get('/integrations/status'),
          api.get('/audit/recent-activity?limit=15'),
        ])
        setAnalytics(analyticsRes.data)
        setIntegrations(integrationsRes.data)
        setActivity(activityRes.data)
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
  const failedActions = (analytics?.likes?.failed || 0) + (analytics?.comments?.failed || 0)
  const totalActions = totalLikes + totalComments
  const successRate = totalActions > 0 ? Math.round((completedActions / totalActions) * 100) : 100

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
        <StatCard
          title="Total Likes"
          value={totalLikes}
          color="blue"
          sub={analytics?.likes?.failed ? `${analytics.likes.failed} failed` : undefined}
        />
        <StatCard
          title="Total Comments"
          value={totalComments}
          color="green"
          sub={analytics?.comments?.failed ? `${analytics.comments.failed} failed` : undefined}
        />
        <StatCard title="Completed" value={completedActions} color="emerald" />
        <StatCard
          title="Success Rate"
          value={successRate}
          color={failedActions > 0 ? 'yellow' : 'emerald'}
          suffix="%"
          sub={failedActions > 0 ? `${failedActions} failed total` : undefined}
        />
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

      {/* Recent Activity Feed */}
      <div className="bg-white rounded-xl shadow-sm p-6">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold">Recent Activity</h2>
          <Link to="/audit" className="text-sm text-primary-600 hover:text-primary-700 font-medium">
            View all
          </Link>
        </div>
        {activity.length === 0 ? (
          <p className="text-sm text-gray-500">No activity yet. Add tracked pages and connect LinkedIn to get started.</p>
        ) : (
          <div className="space-y-3">
            {activity.map((item, idx) => (
              <ActivityItem key={idx} item={item} />
            ))}
          </div>
        )}
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

function StatCard({ title, value, color, suffix, sub }: {
  title: string; value: number; color: string; suffix?: string; sub?: string
}) {
  const colorMap: Record<string, string> = {
    blue: 'bg-blue-50 text-blue-700',
    green: 'bg-green-50 text-green-700',
    emerald: 'bg-emerald-50 text-emerald-700',
    yellow: 'bg-yellow-50 text-yellow-700',
  }
  return (
    <div className={`rounded-xl p-6 ${colorMap[color] || 'bg-gray-50 text-gray-700'}`}>
      <p className="text-sm font-medium opacity-75">{title}</p>
      <p className="text-3xl font-bold mt-1">{value}{suffix}</p>
      {sub && <p className="text-xs mt-1 opacity-60">{sub}</p>}
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

function ActivityItem({ item }: { item: ActivityFeedItem }) {
  const [expanded, setExpanded] = useState(false)

  const icon = item.type.startsWith('like') ? '👍' : item.type.startsWith('comment') ? '💬' : '📋'
  const isFailed = item.type.includes('failed')
  const isCompleted = item.type.includes('completed')

  const formatTime = (ts: string) => {
    const diff = Date.now() - new Date(ts).getTime()
    const mins = Math.floor(diff / 60000)
    if (mins < 1) return 'just now'
    if (mins < 60) return `${mins}m ago`
    const hrs = Math.floor(mins / 60)
    if (hrs < 24) return `${hrs}h ago`
    const days = Math.floor(hrs / 24)
    return `${days}d ago`
  }

  const description = (() => {
    const action = item.type.split('_')[0]
    const status = isFailed ? 'failed to' : ''
    if (action === 'like') return `${item.user_name} ${status} liked a post${item.page_name ? ` on ${item.page_name}` : ''}`
    if (action === 'comment') return `${item.user_name}${status ? "'s comment failed" : "'s comment was posted"}${item.page_name ? ` on ${item.page_name}` : ''}`
    return `${item.user_name}: ${item.type}`
  })()

  return (
    <div className={`flex items-start gap-3 p-3 rounded-lg ${isFailed ? 'bg-red-50' : isCompleted ? 'bg-gray-50' : 'bg-yellow-50'}`}>
      <span className="text-lg flex-shrink-0">{icon}</span>
      <div className="flex-1 min-w-0">
        <p className={`text-sm ${isFailed ? 'text-red-700' : 'text-gray-700'}`}>
          {description}
        </p>
        {isFailed && item.error && (
          <p className="text-xs text-red-500 mt-0.5">{item.error}</p>
        )}
        {item.comment_text && isCompleted && (
          <button
            onClick={() => setExpanded(!expanded)}
            className="text-xs text-primary-600 hover:text-primary-700 mt-0.5"
          >
            {expanded ? 'Hide comment' : 'Show comment'}
          </button>
        )}
        {expanded && item.comment_text && (
          <p className="text-xs text-gray-500 mt-1 italic">&ldquo;{item.comment_text}&rdquo;</p>
        )}
      </div>
      <span className="text-xs text-gray-400 flex-shrink-0">{formatTime(item.timestamp)}</span>
    </div>
  )
}
