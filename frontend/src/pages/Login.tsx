import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import api from '../api/client'
import { getApiErrorMessage } from '../lib/utils'

export default function Login() {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [searchParams] = useSearchParams()

  // Check for error from OAuth callback redirect
  // invalid_state just means the browser state expired — clicking again is fine
  const callbackError = searchParams.get('error')
  const friendlyError =
    callbackError === 'invalid_state'
      ? 'Session expired — please click Sign in again.'
      : callbackError

  const handleLinkedInLogin = async () => {
    setError('')
    setLoading(true)
    // Silently clear URL params so stale ?error= doesn't persist
    window.history.replaceState({}, '', window.location.pathname)
    try {
      const { data } = await api.get('/auth/linkedin')
      window.location.href = data.auth_url
    } catch (err: unknown) {
      setError(getApiErrorMessage(err, 'Failed to start LinkedIn login'))
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50 py-12 px-4">
      <div className="max-w-md w-full space-y-8">
        <div className="text-center">
          <h1 className="text-3xl font-bold text-gray-900">B2B Pulse</h1>
          <p className="mt-2 text-gray-600">Social Engagement Automation</p>
        </div>

        <div className="mt-8 space-y-6 bg-white p-8 rounded-xl shadow-sm">
          {(error || friendlyError) && (
            <div className="bg-red-50 text-red-600 px-4 py-3 rounded-lg text-sm">
              {error || friendlyError}
            </div>
          )}

          <button
            onClick={handleLinkedInLogin}
            disabled={loading}
            className="w-full flex items-center justify-center gap-3 py-3 px-4 border border-transparent rounded-lg shadow-sm text-sm font-medium text-white bg-[#0077B5] hover:bg-[#005f8d] focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-[#0077B5] disabled:opacity-50 transition-colors"
          >
            <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
              <path d="M20.447 20.452h-3.554v-5.569c0-1.328-.027-3.037-1.852-3.037-1.853 0-2.136 1.445-2.136 2.939v5.667H9.351V9h3.414v1.561h.046c.477-.9 1.637-1.85 3.37-1.85 3.601 0 4.267 2.37 4.267 5.455v6.286zM5.337 7.433c-1.144 0-2.063-.926-2.063-2.065 0-1.138.92-2.063 2.063-2.063 1.14 0 2.064.925 2.064 2.063 0 1.139-.925 2.065-2.064 2.065zm1.782 13.019H3.555V9h3.564v11.452zM22.225 0H1.771C.792 0 0 .774 0 1.729v20.542C0 23.227.792 24 1.771 24h20.451C23.2 24 24 23.227 24 22.271V1.729C24 .774 23.2 0 22.222 0h.003z" />
            </svg>
            {loading ? 'Redirecting to LinkedIn...' : 'Sign in with LinkedIn'}
          </button>

          <p className="text-center text-sm text-gray-500">
            Your LinkedIn account will be used for both login and engagement automation.
          </p>

          <p className="text-center text-sm text-gray-600">
            Have an invite link?{' '}
            <Link to="/signup" className="text-primary-600 hover:text-primary-500 font-medium">
              Join a team
            </Link>
          </p>
        </div>
      </div>
    </div>
  )
}
