import { useCallback, useEffect, useState } from 'react'
import api from '../api/client'
import { ConnectionBadge, RoleBadge } from '../components/Badge'
import { PageLoading } from '../components/Loading'
import { useAuth } from '../lib/auth'
import type { OrgInvite, OrgMember, Team as TeamType } from '../lib/types'

export default function Team() {
  const { user } = useAuth()
  const isAdmin = user?.role === 'owner' || user?.role === 'admin'
  const isTeamLeader = user?.role === 'team_leader'
  const canManage = isAdmin || isTeamLeader

  const [members, setMembers] = useState<OrgMember[]>([])
  const [invites, setInvites] = useState<OrgInvite[]>([])
  const [teams, setTeams] = useState<TeamType[]>([])
  const [loading, setLoading] = useState(true)

  // Invite form
  const [showInviteForm, setShowInviteForm] = useState(false)
  const [inviteEmail, setInviteEmail] = useState('')
  const [inviteTeamId, setInviteTeamId] = useState('')
  const [inviteCreating, setInviteCreating] = useState(false)
  const [copiedId, setCopiedId] = useState<string | null>(null)

  // Team form
  const [showTeamForm, setShowTeamForm] = useState(false)
  const [newTeamName, setNewTeamName] = useState('')
  const [teamCreating, setTeamCreating] = useState(false)

  // Team filter
  const [filterTeamId, setFilterTeamId] = useState<string>('all')

  const fetchData = useCallback(async () => {
    try {
      const [membersRes, invitesRes, teamsRes] = await Promise.all([
        api.get('/org/members'),
        canManage ? api.get('/org/invites') : Promise.resolve({ data: [] }),
        api.get('/org/teams'),
      ])
      setMembers(membersRes.data)
      setInvites(invitesRes.data)
      setTeams(teamsRes.data)
    } catch (err) {
      console.error('Failed to load team data', err)
    } finally {
      setLoading(false)
    }
  }, [canManage])

  useEffect(() => {
    fetchData()
  }, [fetchData])

  const handleCreateInvite = async (e: React.FormEvent) => {
    e.preventDefault()
    setInviteCreating(true)
    try {
      await api.post('/org/invites', {
        email: inviteEmail || null,
        team_id: inviteTeamId || null,
      })
      setInviteEmail('')
      setInviteTeamId('')
      setShowInviteForm(false)
      await fetchData()
    } catch (err) {
      console.error('Failed to create invite', err)
    } finally {
      setInviteCreating(false)
    }
  }

  const handleCreateTeam = async (e: React.FormEvent) => {
    e.preventDefault()
    setTeamCreating(true)
    try {
      await api.post('/org/teams', { name: newTeamName })
      setNewTeamName('')
      setShowTeamForm(false)
      await fetchData()
    } catch (err) {
      console.error('Failed to create team', err)
    } finally {
      setTeamCreating(false)
    }
  }

  const handleDeleteTeam = async (teamId: string) => {
    if (!confirm('Are you sure you want to delete this team? Members will be unassigned.')) return
    try {
      await api.delete(`/org/teams/${teamId}`)
      await fetchData()
    } catch (err) {
      console.error('Failed to delete team', err)
    }
  }

  const handleAssignTeam = async (memberId: string, teamId: string | null) => {
    try {
      await api.put(`/org/teams/members/${memberId}/team`, { team_id: teamId })
      await fetchData()
    } catch (err) {
      console.error('Failed to assign team', err)
    }
  }

  const handleRevokeInvite = async (inviteId: string) => {
    try {
      await api.delete(`/org/invites/${inviteId}`)
      await fetchData()
    } catch (err) {
      console.error('Failed to revoke invite', err)
    }
  }

  const handleRemoveMember = async (memberId: string) => {
    if (!confirm('Are you sure you want to remove this member?')) return
    try {
      await api.delete(`/org/members/${memberId}`)
      await fetchData()
    } catch (err) {
      console.error('Failed to remove member', err)
    }
  }

  const copyToClipboard = async (text: string, id: string) => {
    await navigator.clipboard.writeText(text)
    setCopiedId(id)
    setTimeout(() => setCopiedId(null), 2000)
  }

  if (loading) return <PageLoading />

  const pendingInvites = invites.filter((inv) => inv.status === 'pending')

  const filteredMembers =
    filterTeamId === 'all'
      ? members
      : filterTeamId === 'unassigned'
        ? members.filter((m) => !m.team_id)
        : members.filter((m) => m.team_id === filterTeamId)

  return (
    <div className="space-y-8">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Team</h1>
          <p className="mt-1 text-sm text-gray-500">
            Manage your organization members, teams, and invites
          </p>
        </div>
        <div className="flex gap-2">
          {isAdmin && (
            <button
              onClick={() => setShowTeamForm(!showTeamForm)}
              className="inline-flex items-center px-4 py-2 border border-gray-300 rounded-lg shadow-sm text-sm font-medium text-gray-700 bg-white hover:bg-gray-50"
            >
              Create Team
            </button>
          )}
          {canManage && (
            <button
              onClick={() => setShowInviteForm(!showInviteForm)}
              className="inline-flex items-center px-4 py-2 border border-transparent rounded-lg shadow-sm text-sm font-medium text-white bg-primary-600 hover:bg-primary-700"
            >
              Invite Member
            </button>
          )}
        </div>
      </div>

      {/* Team Creation Form */}
      {showTeamForm && (
        <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-200">
          <h3 className="text-sm font-medium text-gray-900 mb-4">Create New Team</h3>
          <form onSubmit={handleCreateTeam} className="flex gap-3">
            <input
              type="text"
              placeholder="Team name"
              value={newTeamName}
              onChange={(e) => setNewTeamName(e.target.value)}
              required
              className="flex-1 px-3 py-2 border border-gray-300 rounded-lg shadow-sm text-sm focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
            />
            <button
              type="submit"
              disabled={teamCreating}
              className="px-4 py-2 bg-primary-600 text-white text-sm font-medium rounded-lg hover:bg-primary-700 disabled:opacity-50"
            >
              {teamCreating ? 'Creating...' : 'Create'}
            </button>
            <button
              type="button"
              onClick={() => setShowTeamForm(false)}
              className="px-4 py-2 bg-gray-100 text-gray-700 text-sm font-medium rounded-lg hover:bg-gray-200"
            >
              Cancel
            </button>
          </form>
        </div>
      )}

      {/* Invite Form */}
      {showInviteForm && (
        <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-200">
          <h3 className="text-sm font-medium text-gray-900 mb-4">Create Invite Link</h3>
          <form onSubmit={handleCreateInvite} className="flex gap-3">
            <input
              type="email"
              placeholder="Email (optional)"
              value={inviteEmail}
              onChange={(e) => setInviteEmail(e.target.value)}
              className="flex-1 px-3 py-2 border border-gray-300 rounded-lg shadow-sm text-sm focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
            />
            <select
              value={inviteTeamId}
              onChange={(e) => setInviteTeamId(e.target.value)}
              className="px-3 py-2 border border-gray-300 rounded-lg shadow-sm text-sm focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
            >
              <option value="">No team</option>
              {teams.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
            </select>
            <button
              type="submit"
              disabled={inviteCreating}
              className="px-4 py-2 bg-primary-600 text-white text-sm font-medium rounded-lg hover:bg-primary-700 disabled:opacity-50"
            >
              {inviteCreating ? 'Creating...' : 'Generate Link'}
            </button>
            <button
              type="button"
              onClick={() => setShowInviteForm(false)}
              className="px-4 py-2 bg-gray-100 text-gray-700 text-sm font-medium rounded-lg hover:bg-gray-200"
            >
              Cancel
            </button>
          </form>
        </div>
      )}

      {/* Teams Overview */}
      {teams.length > 0 && (
        <div className="bg-white rounded-xl shadow-sm border border-gray-200">
          <div className="px-6 py-4 border-b border-gray-200">
            <h3 className="text-sm font-medium text-gray-900">Teams ({teams.length})</h3>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 p-4">
            {teams.map((team) => (
              <div key={team.id} className="border border-gray-200 rounded-lg p-4">
                <div className="flex items-center justify-between">
                  <h4 className="font-medium text-gray-900">{team.name}</h4>
                  {isAdmin && (
                    <button
                      onClick={() => handleDeleteTeam(team.id)}
                      className="text-xs text-red-600 hover:text-red-700"
                    >
                      Delete
                    </button>
                  )}
                </div>
                <p className="text-sm text-gray-500 mt-1">
                  {team.member_count} {team.member_count === 1 ? 'member' : 'members'}
                </p>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Pending Invites */}
      {canManage && pendingInvites.length > 0 && (
        <div className="bg-white rounded-xl shadow-sm border border-gray-200">
          <div className="px-6 py-4 border-b border-gray-200">
            <h3 className="text-sm font-medium text-gray-900">Pending Invites ({pendingInvites.length})</h3>
          </div>
          <div className="divide-y divide-gray-200">
            {pendingInvites.map((invite) => (
              <div key={invite.id} className="px-6 py-4 flex items-center justify-between">
                <div className="flex-1 min-w-0">
                  <p className="text-sm text-gray-900">
                    {invite.email || 'Open invite (anyone with link)'}
                    {invite.team_name && (
                      <span className="ml-2 text-xs px-2 py-0.5 bg-blue-50 text-blue-700 rounded-full">
                        {invite.team_name}
                      </span>
                    )}
                  </p>
                  <div className="mt-1 flex items-center gap-2">
                    <input
                      type="text"
                      readOnly
                      value={invite.invite_url}
                      className="text-xs text-gray-500 bg-gray-50 px-2 py-1 rounded border w-80 truncate"
                    />
                    <button
                      onClick={() => copyToClipboard(invite.invite_url, invite.id)}
                      className="text-xs text-primary-600 hover:text-primary-700 font-medium whitespace-nowrap"
                    >
                      {copiedId === invite.id ? 'Copied!' : 'Copy'}
                    </button>
                  </div>
                  <p className="text-xs text-gray-400 mt-1">
                    Expires {new Date(invite.expires_at).toLocaleDateString()}
                  </p>
                </div>
                <button
                  onClick={() => handleRevokeInvite(invite.id)}
                  className="ml-4 text-sm text-red-600 hover:text-red-700 font-medium"
                >
                  Revoke
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Members Table */}
      <div className="bg-white rounded-xl shadow-sm border border-gray-200">
        <div className="px-6 py-4 border-b border-gray-200 flex items-center justify-between">
          <h3 className="text-sm font-medium text-gray-900">Members ({members.length})</h3>
          {teams.length > 0 && (
            <select
              value={filterTeamId}
              onChange={(e) => setFilterTeamId(e.target.value)}
              className="text-sm border border-gray-300 rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-primary-500"
            >
              <option value="all">All members</option>
              <option value="unassigned">Unassigned</option>
              {teams.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
            </select>
          )}
        </div>
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Name
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Role
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Team
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Integrations
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Joined
                </th>
                {isAdmin && (
                  <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">
                    Actions
                  </th>
                )}
              </tr>
            </thead>
            <tbody className="bg-white divide-y divide-gray-200">
              {filteredMembers.map((member) => (
                <tr key={member.id} className={!member.is_active ? 'opacity-50' : ''}>
                  <td className="px-6 py-4 whitespace-nowrap">
                    <div>
                      <p className="text-sm font-medium text-gray-900">
                        {member.full_name}
                        {member.id === user?.id && (
                          <span className="ml-2 text-xs text-gray-400">(you)</span>
                        )}
                      </p>
                      <p className="text-sm text-gray-500">{member.email}</p>
                    </div>
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap">
                    <RoleBadge role={member.role} />
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap">
                    {isAdmin ? (
                      <select
                        value={member.team_id || ''}
                        onChange={(e) => handleAssignTeam(member.id, e.target.value || null)}
                        className="text-sm border border-gray-200 rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-primary-500"
                      >
                        <option value="">No team</option>
                        {teams.map((t) => (
                          <option key={t.id} value={t.id}>
                            {t.name}
                          </option>
                        ))}
                      </select>
                    ) : (
                      <span className="text-sm text-gray-600">{member.team_name || '—'}</span>
                    )}
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap">
                    <div className="flex gap-1.5">
                      <ConnectionBadge platform="LinkedIn" connected={member.integrations.includes('linkedin')} />
                      <ConnectionBadge platform="Meta" connected={member.integrations.includes('meta')} />
                    </div>
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                    {new Date(member.created_at).toLocaleDateString()}
                  </td>
                  {isAdmin && (
                    <td className="px-6 py-4 whitespace-nowrap text-right">
                      {member.id !== user?.id && member.role !== 'owner' && member.is_active && (
                        <button
                          onClick={() => handleRemoveMember(member.id)}
                          className="text-sm text-red-600 hover:text-red-700 font-medium"
                        >
                          Remove
                        </button>
                      )}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
