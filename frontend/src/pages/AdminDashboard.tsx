import { useEffect, useState } from 'react'
import { Building2, Users, Zap, Link as LinkIcon } from 'lucide-react'
import api from '../api/client'
import { SectionLoading } from '../components/Loading'
import { getApiErrorMessage } from '../lib/utils'

interface OrgSummary {
  id: string
  name: string
  member_count: number
  team_count: number
  created_at: string
}

interface PlatformStats {
  total_orgs: number
  total_users: number
  active_users: number
  total_engagements: number
  active_integrations: number
}

export default function AdminDashboard() {
  const [orgs, setOrgs] = useState<OrgSummary[]>([])
  const [stats, setStats] = useState<PlatformStats | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    Promise.all([api.get('/admin/orgs'), api.get('/admin/stats')])
      .then(([orgsRes, statsRes]) => {
        setOrgs(orgsRes.data)
        setStats(statsRes.data)
      })
      .catch((err) => setError(getApiErrorMessage(err, 'Failed to load admin data')))
      .finally(() => setLoading(false))
  }, [])

  if (loading) return <SectionLoading />

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold text-gray-900">Platform Admin</h1>

      {error && <div className="bg-red-50 text-red-600 px-4 py-3 rounded-lg text-sm">{error}</div>}

      {/* Stats Grid */}
      {stats && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <StatCard icon={<Building2 className="w-5 h-5" />} label="Organizations" value={stats.total_orgs} />
          <StatCard icon={<Users className="w-5 h-5" />} label="Active Users" value={stats.active_users} />
          <StatCard icon={<Zap className="w-5 h-5" />} label="Engagements" value={stats.total_engagements} />
          <StatCard icon={<LinkIcon className="w-5 h-5" />} label="Integrations" value={stats.active_integrations} />
        </div>
      )}

      {/* Orgs Table */}
      <div className="bg-white rounded-xl shadow-sm overflow-hidden">
        <div className="px-6 py-4 border-b border-gray-200">
          <h2 className="text-lg font-semibold text-gray-900">All Organizations</h2>
        </div>
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Organization
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Members
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Teams
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Created
                </th>
              </tr>
            </thead>
            <tbody className="bg-white divide-y divide-gray-200">
              {orgs.map((org) => (
                <tr key={org.id} className="hover:bg-gray-50">
                  <td className="px-6 py-4 whitespace-nowrap text-sm font-medium text-gray-900">{org.name}</td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-600">{org.member_count}</td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-600">{org.team_count}</td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                    {new Date(org.created_at).toLocaleDateString()}
                  </td>
                </tr>
              ))}
              {orgs.length === 0 && (
                <tr>
                  <td colSpan={4} className="px-6 py-8 text-center text-sm text-gray-500">
                    No organizations yet.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

function StatCard({ icon, label, value }: { icon: React.ReactNode; label: string; value: number }) {
  return (
    <div className="bg-white rounded-xl shadow-sm p-4">
      <div className="flex items-center gap-3">
        <div className="text-primary-600">{icon}</div>
        <div>
          <p className="text-2xl font-bold text-gray-900">{value.toLocaleString()}</p>
          <p className="text-sm text-gray-500">{label}</p>
        </div>
      </div>
    </div>
  )
}
