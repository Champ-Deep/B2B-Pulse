import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import api from '../api/client'
import { Spinner } from '../components/Loading'
import { getApiErrorMessage } from '../lib/utils'

export default function Signup() {
  const [searchParams] = useSearchParams()
  const inviteCode = searchParams.get('invite')
  const errorParam = searchParams.get('error')

  const [error, setError] = useState(errorParam || '')
  const [loading, setLoading] = useState(false)

  const [inviteInfo, setInviteInfo] = useState<{
    valid: boolean
    org_name?: string
    email?: string
    team_name?: string
  } | null>(null)
  const [inviteLoading, setInviteLoading] = useState(!!inviteCode)

  useEffect(() => {
    if (inviteCode) {
      api
        .get(`/org/invites/validate/${inviteCode}`)
        .then(({ data }) => setInviteInfo(data))
        .catch(() => setInviteInfo({ valid: false }))
        .finally(() => setInviteLoading(false))
    }
  }, [inviteCode])

  const handleLinkedInSignup = async () => {
    setError('')
    setLoading(true)
    try {
      const params = inviteCode ? `?invite_code=${inviteCode}` : ''
      const { data } = await api.get(`/auth/linkedin${params}`)
      window.location.href = data.auth_url
    } catch (err: unknown) {
      setError(getApiErrorMessage(err, 'Failed to start LinkedIn signup'))
      setLoading(false)
    }
  }

  if (inviteLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50">
        <Spinner />
      </div>
    )
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50 py-12 px-4">
      <div className="max-w-md w-full space-y-8">
        <div className="text-center">
          <h1 className="text-3xl font-bold text-gray-900">B2B Pulse</h1>
          <p className="mt-2 text-gray-600">
            {inviteInfo?.valid
              ? `Join ${inviteInfo.org_name}${inviteInfo.team_name ? ` - ${inviteInfo.team_name}` : ''}`
              : 'Create your account'}
          </p>
        </div>

        <div className="mt-8 space-y-6 bg-white p-8 rounded-xl shadow-sm">
          {error && (
            <div className="bg-red-50 text-red-600 px-4 py-3 rounded-lg text-sm">{error}</div>
          )}

          {inviteCode && inviteInfo && !inviteInfo.valid && (
            <div className="bg-amber-50 text-amber-700 px-4 py-3 rounded-lg text-sm">
              This invite link is invalid or expired. You can still create a new organization below.
            </div>
          )}

          {inviteInfo?.valid && (
            <div className="bg-green-50 text-green-700 px-4 py-3 rounded-lg text-sm">
              You've been invited to join <span className="font-semibold">{inviteInfo.org_name}</span>
              {inviteInfo.team_name && (
                <>
                  {' '}
                  on the <span className="font-semibold">{inviteInfo.team_name}</span> team
                </>
              )}
              . Sign in with LinkedIn to get started.
            </div>
          )}

          <button
            onClick={handleLinkedInSignup}
            disabled={loading}
            className="w-full flex items-center justify-center gap-3 py-3 px-4 border border-transparent rounded-lg shadow-sm text-sm font-medium text-white bg-[#0077B5] hover:bg-[#005f8d] focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-[#0077B5] disabled:opacity-50 transition-colors"
          >
            <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
              <path d="M20.447 20.452h-3.554v-5.569c0-1.328-.027-3.037-1.852-3.037-1.853 0-2.136 1.445-2.136 2.939v5.667H9.351V9h3.414v1.561h.046c.477-.9 1.637-1.85 3.37-1.85 3.601 0 4.267 2.37 4.267 5.455v6.286zM5.337 7.433c-1.144 0-2.063-.926-2.063-2.065 0-1.138.92-2.063 2.063-2.063 1.14 0 2.064.925 2.064 2.063 0 1.139-.925 2.065-2.064 2.065zm1.782 13.019H3.555V9h3.564v11.452zM22.225 0H1.771C.792 0 0 .774 0 1.729v20.542C0 23.227.792 24 1.771 24h20.451C23.2 24 24 23.227 24 22.271V1.729C24 .774 23.2 0 22.222 0h.003z" />
            </svg>
            {loading
              ? 'Redirecting to LinkedIn...'
              : inviteInfo?.valid
                ? 'Join team with LinkedIn'
                : 'Create account with LinkedIn'}
          </button>

          <p className="text-center text-sm text-gray-500">
            Your LinkedIn account will be used for both login and engagement automation.
          </p>

          <p className="text-center text-sm text-gray-600">
            Already have an account?{' '}
            <Link to="/login" className="text-primary-600 hover:text-primary-500 font-medium">
              Sign in
            </Link>
          </p>
        </div>
      </div>
    </div>
  )
}
