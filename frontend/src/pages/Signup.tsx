import { useEffect, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import api from '../api/client'
import { Spinner } from '../components/Loading'
import { useAuth } from '../lib/auth'
import { getApiErrorMessage } from '../lib/utils'

export default function Signup() {
  const [searchParams] = useSearchParams()
  const inviteCode = searchParams.get('invite')

  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [fullName, setFullName] = useState('')
  const [orgName, setOrgName] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const [inviteInfo, setInviteInfo] = useState<{
    valid: boolean
    org_name?: string
    email?: string
  } | null>(null)
  const [inviteLoading, setInviteLoading] = useState(!!inviteCode)

  const { signup } = useAuth()
  const navigate = useNavigate()

  useEffect(() => {
    if (inviteCode) {
      api
        .get(`/org/invites/validate/${inviteCode}`)
        .then(({ data }) => {
          setInviteInfo(data)
          if (data.email) setEmail(data.email)
        })
        .catch(() => setInviteInfo({ valid: false }))
        .finally(() => setInviteLoading(false))
    }
  }, [inviteCode])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      await signup(email, password, fullName, orgName, inviteCode || undefined)
      navigate(inviteCode ? '/settings' : '/onboarding')
    } catch (err: unknown) {
      setError(getApiErrorMessage(err, 'Signup failed'))
    } finally {
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
          <h1 className="text-3xl font-bold text-gray-900">AutoEngage</h1>
          <p className="mt-2 text-gray-600">
            {inviteInfo?.valid
              ? `Join ${inviteInfo.org_name}`
              : 'Create your account'}
          </p>
        </div>

        <form
          className="mt-8 space-y-6 bg-white p-8 rounded-xl shadow-sm"
          onSubmit={handleSubmit}
        >
          {error && (
            <div className="bg-red-50 text-red-600 px-4 py-3 rounded-lg text-sm">
              {error}
            </div>
          )}

          {inviteCode && inviteInfo && !inviteInfo.valid && (
            <div className="bg-amber-50 text-amber-700 px-4 py-3 rounded-lg text-sm">
              This invite link is invalid or expired. You can still create a new
              organization below.
            </div>
          )}

          {inviteInfo?.valid && (
            <div className="bg-green-50 text-green-700 px-4 py-3 rounded-lg text-sm">
              You've been invited to join{' '}
              <span className="font-semibold">{inviteInfo.org_name}</span>.
              Create your account to get started.
            </div>
          )}

          <div className="space-y-4">
            <div>
              <label
                htmlFor="fullName"
                className="block text-sm font-medium text-gray-700"
              >
                Full Name
              </label>
              <input
                id="fullName"
                type="text"
                required
                value={fullName}
                onChange={(e) => setFullName(e.target.value)}
                className="mt-1 block w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
              />
            </div>

            {/* Hide org name when joining via invite */}
            {!(inviteInfo?.valid) && (
              <div>
                <label
                  htmlFor="orgName"
                  className="block text-sm font-medium text-gray-700"
                >
                  Organization Name
                </label>
                <input
                  id="orgName"
                  type="text"
                  required
                  value={orgName}
                  onChange={(e) => setOrgName(e.target.value)}
                  placeholder="e.g., LakeB2B"
                  className="mt-1 block w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
                />
              </div>
            )}

            <div>
              <label
                htmlFor="email"
                className="block text-sm font-medium text-gray-700"
              >
                Email
              </label>
              <input
                id="email"
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                readOnly={!!inviteInfo?.email}
                className={`mt-1 block w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-primary-500 ${
                  inviteInfo?.email ? 'bg-gray-50 text-gray-500' : ''
                }`}
              />
            </div>
            <div>
              <label
                htmlFor="password"
                className="block text-sm font-medium text-gray-700"
              >
                Password
              </label>
              <input
                id="password"
                type="password"
                required
                minLength={8}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="mt-1 block w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
              />
            </div>
          </div>

          <button
            type="submit"
            disabled={loading}
            className="w-full flex justify-center py-2.5 px-4 border border-transparent rounded-lg shadow-sm text-sm font-medium text-white bg-primary-600 hover:bg-primary-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-primary-500 disabled:opacity-50"
          >
            {loading
              ? 'Creating account...'
              : inviteInfo?.valid
                ? 'Join team'
                : 'Create account'}
          </button>

          <p className="text-center text-sm text-gray-600">
            Already have an account?{' '}
            <Link
              to="/login"
              className="text-primary-600 hover:text-primary-500 font-medium"
            >
              Sign in
            </Link>
          </p>
        </form>
      </div>
    </div>
  )
}
