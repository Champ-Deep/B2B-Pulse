import { useCallback, useEffect, useState } from 'react'
import api from '../api/client'
import { ConnectionBadge, RoleBadge } from '../components/Badge'
import { PageLoading } from '../components/Loading'
import { useAuth } from '../lib/auth'
import type { OrgInvite, OrgMember } from '../lib/types'

export default function Team() {
  const { user } = useAuth()
  const isAdmin = user?.role === 'owner' || user?.role === 'admin'

  const [members, setMembers] = useState<OrgMember[]>([])
  const [invites, setInvites] = useState<OrgInvite[]>([])
  const [loading, setLoading] = useState(true)

  // Invite form
  const [showInviteForm, setShowInviteForm] = useState(false)
  const [inviteEmail, setInviteEmail] = useState('')
  const [inviteCreating, setInviteCreating] = useState(false)
  const [copiedId, setCopiedId] = useState<string | null>(null)

  const fetchData = useCallback(async () => {
    try {
      const [membersRes, invitesRes] = await Promise.all([
        api.get('/org/members'),
        isAdmin ? api.get('/org/invites') : Promise.resolve({ data: [] }),
      ])
      setMembers(membersRes.data)
      setInvites(invitesRes.data)
    } catch (err) {
      console.error('Failed to load team data', err)
    } finally {
      setLoading(false)
    }
  }, [isAdmin])

  useEffect(() => {
    fetchData()
  }, [fetchData])

  const handleCreateInvite = async (e: React.FormEvent) => {
    e.preventDefault()
    setInviteCreating(true)
    try {
      await api.post('/org/invites', {
        email: inviteEmail || null,
      })
      setInviteEmail('')
      setShowInviteForm(false)
      await fetchData()
    } catch (err) {
      console.error('Failed to create invite', err)
    } finally {
      setInviteCreating(false)
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

  if (loading) {
    return <PageLoading />
  }

  const pendingInvites = invites.filter((inv) => inv.status === 'pending')

  return (
    <div className="space-y-8">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Team</h1>
          <p className="mt-1 text-sm text-gray-500">
            Manage your organization members and invite new team members
          </p>
        </div>
        {isAdmin && (
          <button
            onClick={() => setShowInviteForm(!showInviteForm)}
            className="inline-flex items-center px-4 py-2 border border-transparent rounded-lg shadow-sm text-sm font-medium text-white bg-primary-600 hover:bg-primary-700"
          >
            <svg
              className="w-4 h-4 mr-2"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M12 6v6m0 0v6m0-6h6m-6 0H6"
              />
            </svg>
            Invite Member
          </button>
        )}
      </div>

      {/* Invite Form */}
      {showInviteForm && (
        <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-200">
          <h3 className="text-sm font-medium text-gray-900 mb-4">
            Create Invite Link
          </h3>
          <form onSubmit={handleCreateInvite} className="flex gap-3">
            <input
              type="email"
              placeholder="Email (optional — leave blank for open link)"
              value={inviteEmail}
              onChange={(e) => setInviteEmail(e.target.value)}
              className="flex-1 px-3 py-2 border border-gray-300 rounded-lg shadow-sm text-sm focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
            />
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

      {/* Pending Invites */}
      {isAdmin && pendingInvites.length > 0 && (
        <div className="bg-white rounded-xl shadow-sm border border-gray-200">
          <div className="px-6 py-4 border-b border-gray-200">
            <h3 className="text-sm font-medium text-gray-900">
              Pending Invites ({pendingInvites.length})
            </h3>
          </div>
          <div className="divide-y divide-gray-200">
            {pendingInvites.map((invite) => (
              <div
                key={invite.id}
                className="px-6 py-4 flex items-center justify-between"
              >
                <div className="flex-1 min-w-0">
                  <p className="text-sm text-gray-900">
                    {invite.email || 'Open invite (anyone with link)'}
                  </p>
                  <div className="mt-1 flex items-center gap-2">
                    <input
                      type="text"
                      readOnly
                      value={invite.invite_url}
                      className="text-xs text-gray-500 bg-gray-50 px-2 py-1 rounded border w-80 truncate"
                    />
                    <button
                      onClick={() =>
                        copyToClipboard(invite.invite_url, invite.id)
                      }
                      className="text-xs text-primary-600 hover:text-primary-700 font-medium whitespace-nowrap"
                    >
                      {copiedId === invite.id ? 'Copied!' : 'Copy'}
                    </button>
                  </div>
                  <p className="text-xs text-gray-400 mt-1">
                    Expires{' '}
                    {new Date(invite.expires_at).toLocaleDateString()}
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
        <div className="px-6 py-4 border-b border-gray-200">
          <h3 className="text-sm font-medium text-gray-900">
            Members ({members.length})
          </h3>
        </div>
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
            {members.map((member) => (
              <tr
                key={member.id}
                className={!member.is_active ? 'opacity-50' : ''}
              >
                <td className="px-6 py-4 whitespace-nowrap">
                  <div>
                    <p className="text-sm font-medium text-gray-900">
                      {member.full_name}
                      {member.id === user?.id && (
                        <span className="ml-2 text-xs text-gray-400">
                          (you)
                        </span>
                      )}
                    </p>
                    <p className="text-sm text-gray-500">{member.email}</p>
                  </div>
                </td>
                <td className="px-6 py-4 whitespace-nowrap">
                  <RoleBadge role={member.role} />
                </td>
                <td className="px-6 py-4 whitespace-nowrap">
                  <div className="flex gap-1.5">
                    <ConnectionBadge
                      platform="LinkedIn"
                      connected={member.integrations.includes('linkedin')}
                    />
                    <ConnectionBadge
                      platform="Meta"
                      connected={member.integrations.includes('meta')}
                    />
                  </div>
                </td>
                <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                  {new Date(member.created_at).toLocaleDateString()}
                </td>
                {isAdmin && (
                  <td className="px-6 py-4 whitespace-nowrap text-right">
                    {member.id !== user?.id &&
                      member.role !== 'owner' &&
                      member.is_active && (
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
  )
}
