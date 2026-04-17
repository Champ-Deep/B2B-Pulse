import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import api from '../api/client'
import { ROUTES } from '../lib/routes'
import {
  hasExtensionRuntime,
  pairExtension,
  pingExtension,
} from '../lib/extensionBridge'

type SessionStatus = {
  has_session_cookies: boolean
  user_name: string | null
  session_expires_at: string | null
  last_session_check: string | null
  needs_reconnect?: boolean
  source?: string | null
  is_active?: boolean
}

type Phase =
  | 'idle'
  | 'extension_detecting'
  | 'extension_pairing'
  | 'extension_waiting_sync'
  | 'connected'
  | 'playwright_login'
  | 'playwright_needs_verification'
  | 'paste'

export default function OnboardingLinkedIn() {
  const navigate = useNavigate()
  const [status, setStatus] = useState<SessionStatus | null>(null)
  const [phase, setPhase] = useState<Phase>('idle')
  const [error, setError] = useState<string | null>(null)
  const [extensionInstalled, setExtensionInstalled] = useState<boolean | null>(null)

  // Playwright form state
  const [pwEmail, setPwEmail] = useState('')
  const [pwPassword, setPwPassword] = useState('')
  const [pwCode, setPwCode] = useState('')
  const [pwSessionId, setPwSessionId] = useState<string | null>(null)
  const [pwBusy, setPwBusy] = useState(false)

  // Paste form state
  const [liAt, setLiAt] = useState('')
  const [pasteBusy, setPasteBusy] = useState(false)

  const pollTimer = useRef<number | null>(null)

  const fetchStatus = async () => {
    const { data } = await api.get<SessionStatus>('/integrations/linkedin/session-status')
    setStatus(data)
    if (data.has_session_cookies && !data.needs_reconnect) {
      setPhase('connected')
      stopPolling()
    }
    return data
  }

  const startPolling = () => {
    if (pollTimer.current !== null) return
    pollTimer.current = window.setInterval(fetchStatus, 2000)
  }

  const stopPolling = () => {
    if (pollTimer.current !== null) {
      window.clearInterval(pollTimer.current)
      pollTimer.current = null
    }
  }

  useEffect(() => {
    fetchStatus().catch(() => undefined)
    // Best-effort ping the extension so we can tell the user whether it's installed.
    if (hasExtensionRuntime()) {
      pingExtension().then(setExtensionInstalled).catch(() => setExtensionInstalled(false))
    } else {
      setExtensionInstalled(false)
    }
    return stopPolling
  }, [])

  const handleExtensionConnect = async () => {
    setError(null)
    setPhase('extension_pairing')
    try {
      const { data } = await api.post<{ pairing_token: string; api_base: string }>(
        '/integrations/extension/pair',
      )
      const result = await pairExtension(data.pairing_token, data.api_base)
      if (!result.ok) {
        if (result.status === 'not_installed' || result.status === 'no_response') {
          setError(
            'We could not reach the ChampMail Connector extension. Make sure it is installed and reload this page.',
          )
          setPhase('idle')
          return
        }
        setError(`Pairing failed (${result.status ?? 'unknown'}). Try again or use another option below.`)
        setPhase('idle')
        return
      }
      setPhase('extension_waiting_sync')
      startPolling()
      // Also fetch once immediately — the extension usually syncs before the poll fires.
      await fetchStatus()
    } catch (err) {
      console.error(err)
      setError('Could not start extension pairing. Please try again.')
      setPhase('idle')
    }
  }

  const handlePlaywrightStart = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    setPwBusy(true)
    try {
      const { data } = await api.post('/integrations/linkedin/login-start', {
        email: pwEmail,
        password: pwPassword,
      })
      if (data.status === 'success') {
        await fetchStatus()
      } else if (data.status === 'needs_verification') {
        setPwSessionId(data.session_id)
        setPhase('playwright_needs_verification')
      } else if (data.status === 'captcha') {
        setError('LinkedIn asked for a captcha. Try the extension or the paste option below.')
      } else {
        setError(data.error || 'Login failed. Try the extension or paste option.')
      }
    } catch (err: unknown) {
      const detail = (err as { response?: { status?: number; data?: { detail?: unknown } } })
        ?.response
      if (detail?.status === 429) {
        const d = detail.data?.detail as { message?: string; retry_after_seconds?: number } | string | undefined
        if (d && typeof d === 'object') {
          const mins = Math.ceil((d.retry_after_seconds ?? 60) / 60)
          setError(`${d.message ?? 'Too many attempts.'} Try again in ${mins} minute(s).`)
        } else {
          setError(typeof d === 'string' ? d : 'Too many attempts, please wait and try again.')
        }
      } else {
        setError('Login failed. Please try the extension or paste option.')
      }
    } finally {
      setPwBusy(false)
    }
  }

  const handlePlaywrightVerify = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!pwSessionId) return
    setError(null)
    setPwBusy(true)
    try {
      const { data } = await api.post('/integrations/linkedin/login-verify', {
        session_id: pwSessionId,
        code: pwCode,
      })
      if (data.status === 'success') {
        await fetchStatus()
      } else {
        setError(data.error || 'Verification failed. Try again or use another method.')
      }
    } catch {
      setError('Verification failed. Please try again.')
    } finally {
      setPwBusy(false)
    }
  }

  const handlePaste = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    setPasteBusy(true)
    try {
      await api.post('/integrations/linkedin/session-cookies', { li_at: liAt.trim() })
      await fetchStatus()
    } catch (err: unknown) {
      const msg =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
        'Could not save cookie. Make sure it is valid and try again.'
      setError(msg)
    } finally {
      setPasteBusy(false)
    }
  }

  if (phase === 'connected' || (status?.has_session_cookies && !status?.needs_reconnect)) {
    return (
      <div className="max-w-2xl mx-auto space-y-6">
        <div className="bg-white rounded-xl shadow-sm p-8 text-center">
          <div className="w-12 h-12 rounded-full bg-green-100 text-green-700 mx-auto flex items-center justify-center text-2xl">
            ✓
          </div>
          <h1 className="text-2xl font-bold text-gray-900 mt-4">LinkedIn connected</h1>
          <p className="text-gray-500 mt-2">
            {status?.user_name ? `Signed in as ${status.user_name}.` : null} Source:{' '}
            <code className="text-xs">{status?.source ?? 'unknown'}</code>
          </p>
          <div className="flex gap-3 justify-center mt-6">
            <button
              onClick={() => navigate(ROUTES.ONBOARDING)}
              className="px-5 py-2.5 bg-primary-600 text-white rounded-lg text-sm font-medium hover:bg-primary-700"
            >
              Continue to tone setup
            </button>
            <Link
              to={ROUTES.SETTINGS}
              className="px-4 py-2.5 text-sm text-gray-600 hover:text-gray-900"
            >
              Go to settings
            </Link>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Connect your LinkedIn</h1>
        <p className="text-gray-500 mt-1">
          Pick the option that works for you. The extension is smoothest — your password never
          leaves your machine and the connection refreshes automatically.
        </p>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 text-sm px-4 py-3 rounded-lg">
          {error}
        </div>
      )}

      {/* Primary: Extension */}
      <div className="bg-white rounded-xl shadow-sm p-6 border-2 border-primary-500">
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="inline-block text-xs uppercase tracking-wide text-primary-700 bg-primary-50 px-2 py-0.5 rounded mb-2">
              Recommended
            </div>
            <h2 className="text-lg font-semibold">Install ChampMail Connector</h2>
            <p className="text-sm text-gray-500 mt-1">
              One-click extension that reads your own LinkedIn session cookie and syncs it to
              ChampMail. Auto-refreshes — no reconnect emails.
            </p>
          </div>
        </div>
        <div className="mt-4 flex flex-wrap items-center gap-3">
          {extensionInstalled === false && (
            <span className="text-xs text-amber-700 bg-amber-50 px-2 py-1 rounded">
              Extension not detected — install it, reload this page, then continue.
            </span>
          )}
          {extensionInstalled && (
            <span className="text-xs text-green-700 bg-green-50 px-2 py-1 rounded">
              Extension detected ✓
            </span>
          )}
          <button
            onClick={handleExtensionConnect}
            disabled={phase === 'extension_pairing' || phase === 'extension_waiting_sync'}
            className="px-5 py-2.5 bg-primary-600 text-white rounded-lg text-sm font-medium hover:bg-primary-700 disabled:opacity-60"
          >
            {phase === 'extension_pairing'
              ? 'Pairing…'
              : phase === 'extension_waiting_sync'
                ? 'Waiting for first sync…'
                : 'Connect with extension'}
          </button>
          <a
            href="https://chromewebstore.google.com/"
            target="_blank"
            rel="noreferrer"
            className="text-sm text-gray-500 hover:text-gray-900"
          >
            How to install →
          </a>
        </div>
      </div>

      {/* Secondary: Playwright */}
      <div className="bg-white rounded-xl shadow-sm p-6">
        <h2 className="text-lg font-semibold">Sign in with email & password</h2>
        <p className="text-sm text-gray-500 mt-1">
          We'll log you in via a secure headless browser on our server. 2FA supported. No password
          is stored — only the resulting session cookie.
        </p>
        {phase !== 'playwright_needs_verification' ? (
          <form onSubmit={handlePlaywrightStart} className="mt-4 space-y-3">
            <input
              type="email"
              required
              autoComplete="username"
              placeholder="LinkedIn email"
              value={pwEmail}
              onChange={(e) => setPwEmail(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm"
            />
            <input
              type="password"
              required
              autoComplete="current-password"
              placeholder="Password"
              value={pwPassword}
              onChange={(e) => setPwPassword(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm"
            />
            <button
              type="submit"
              disabled={pwBusy}
              className="px-5 py-2.5 bg-gray-900 text-white rounded-lg text-sm font-medium hover:bg-gray-800 disabled:opacity-60"
            >
              {pwBusy ? 'Signing in…' : 'Sign in'}
            </button>
          </form>
        ) : (
          <form onSubmit={handlePlaywrightVerify} className="mt-4 space-y-3">
            <p className="text-sm text-gray-700">
              LinkedIn asked for a verification code. Enter the one they emailed / texted you.
            </p>
            <input
              type="text"
              required
              inputMode="numeric"
              autoComplete="one-time-code"
              placeholder="Verification code"
              value={pwCode}
              onChange={(e) => setPwCode(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm"
            />
            <div className="flex gap-3">
              <button
                type="submit"
                disabled={pwBusy}
                className="px-5 py-2.5 bg-gray-900 text-white rounded-lg text-sm font-medium hover:bg-gray-800 disabled:opacity-60"
              >
                {pwBusy ? 'Verifying…' : 'Verify'}
              </button>
              <button
                type="button"
                onClick={() => {
                  setPwSessionId(null)
                  setPhase('idle')
                }}
                className="px-4 py-2.5 text-sm text-gray-500 hover:text-gray-700"
              >
                Cancel
              </button>
            </div>
          </form>
        )}
      </div>

      {/* Advanced: paste */}
      <details className="bg-white rounded-xl shadow-sm p-6">
        <summary className="cursor-pointer text-sm font-medium text-gray-700">
          Advanced: paste a session cookie manually
        </summary>
        <form onSubmit={handlePaste} className="mt-4 space-y-3">
          <p className="text-xs text-gray-500">
            Open linkedin.com while logged in → DevTools → Application → Cookies → copy the value
            of <code>li_at</code>.
          </p>
          <textarea
            required
            rows={3}
            placeholder="Paste li_at value"
            value={liAt}
            onChange={(e) => setLiAt(e.target.value)}
            className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm font-mono"
          />
          <button
            type="submit"
            disabled={pasteBusy}
            className="px-5 py-2.5 bg-gray-700 text-white rounded-lg text-sm font-medium hover:bg-gray-900 disabled:opacity-60"
          >
            {pasteBusy ? 'Saving…' : 'Save cookie'}
          </button>
        </form>
      </details>
    </div>
  )
}
