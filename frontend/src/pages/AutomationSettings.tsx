import { useEffect, useState } from 'react'
import api from '../api/client'
import { StatusBadge } from '../components/Badge'
import { SectionLoading } from '../components/Loading'
import type { AutomationSettings, AvoidPhrase, IntegrationStatus } from '../lib/types'

export default function SettingsPage() {
  const [integrations, setIntegrations] = useState<IntegrationStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [connectingLinkedIn, setConnectingLinkedIn] = useState(false)
  const [connectingMeta, setConnectingMeta] = useState(false)

  // Automation settings state
  const [settings, setSettings] = useState<AutomationSettings>({
    risk_profile: 'safe',
    quiet_hours_enabled: true,
    quiet_hours_start: '22:00',
    quiet_hours_end: '07:00',
    polling_interval: 300,
  })
  const [saving, setSaving] = useState(false)
  const [saveMessage, setSaveMessage] = useState('')

  // LinkedIn session cookie state
  const [liAtCookie, setLiAtCookie] = useState('')
  const [savingSession, setSavingSession] = useState(false)
  const [sessionMessage, setSessionMessage] = useState('')
  const [showCookieGuide, setShowCookieGuide] = useState(false)

  // LinkedIn in-app login state
  const [showLoginForm, setShowLoginForm] = useState(false)
  const [loginEmail, setLoginEmail] = useState('')
  const [loginPassword, setLoginPassword] = useState('')
  const [loginLoading, setLoginLoading] = useState(false)
  const [loginMessage, setLoginMessage] = useState('')
  const [loginSessionId, setLoginSessionId] = useState<string | null>(null)
  const [verificationCode, setVerificationCode] = useState('')

  // WhatsApp QR state
  const [qrCode, setQrCode] = useState<string | null>(null)
  const [whatsappStatus, setWhatsappStatus] = useState<string>('initializing')

  // Avoid phrases state
  const [avoidPhrases, setAvoidPhrases] = useState<AvoidPhrase[]>([])
  const [newPhrase, setNewPhrase] = useState('')
  const [addingPhrase, setAddingPhrase] = useState(false)

  useEffect(() => {
    const fetchData = async () => {
      try {
        const [statusRes, settingsRes, phrasesRes] = await Promise.all([
          api.get('/integrations/status'),
          api.get('/automation/settings'),
          api.get('/automation/avoid-phrases'),
        ])
        setIntegrations(statusRes.data)
        setSettings(settingsRes.data)
        setAvoidPhrases(phrasesRes.data)
      } catch (err) {
        console.error('Failed to load settings:', err)
      } finally {
        setLoading(false)
      }
    }
    fetchData()
  }, [])

  // Poll for WhatsApp QR
  useEffect(() => {
    if (integrations?.whatsapp.connected) return

    const SIDECAR_URL = import.meta.env.VITE_WHATSAPP_SIDECAR_URL || 'http://localhost:3001'

    const fetchQR = async () => {
      try {
        const res = await fetch(`${SIDECAR_URL}/qr`)
        const data = await res.json()
        setWhatsappStatus(data.status)
        if (data.ready) {
          setQrCode(null)
          setIntegrations((prev) =>
            prev ? { ...prev, whatsapp: { connected: true, active: true } } : prev
          )
        } else if (data.qr) {
          setQrCode(data.qr)
        }
      } catch {
        setWhatsappStatus('unavailable')
      }
    }

    fetchQR()
    const interval = setInterval(fetchQR, 3000)
    return () => clearInterval(interval)
  }, [integrations?.whatsapp.connected])

  const handleConnectLinkedIn = async () => {
    setConnectingLinkedIn(true)
    try {
      const { data } = await api.get('/integrations/linkedin/auth-url')
      window.location.href = data.auth_url
    } catch (err) {
      console.error('Failed to get LinkedIn auth URL:', err)
      setConnectingLinkedIn(false)
    }
  }

  const handleConnectMeta = async () => {
    setConnectingMeta(true)
    try {
      const { data } = await api.get('/integrations/meta/auth-url')
      window.location.href = data.auth_url
    } catch (err) {
      console.error('Failed to get Meta auth URL:', err)
      setConnectingMeta(false)
    }
  }

  const handleSaveSession = async () => {
    if (!liAtCookie.trim()) return
    setSavingSession(true)
    setSessionMessage('')
    try {
      await api.post('/integrations/linkedin/session-cookies', { li_at: liAtCookie.trim() })
      setSessionMessage('Session saved and verified!')
      setIntegrations((prev) =>
        prev ? { ...prev, linkedin: { ...prev.linkedin, has_session_cookies: true } } : prev,
      )
      setLiAtCookie('')
      setTimeout(() => setSessionMessage(''), 5000)
    } catch {
      setSessionMessage('Invalid or expired cookie. Make sure you copied the full li_at value.')
    } finally {
      setSavingSession(false)
    }
  }

  const handleLoginStart = async () => {
    if (!loginEmail.trim() || !loginPassword.trim()) return
    setLoginLoading(true)
    setLoginMessage('')
    try {
      const { data } = await api.post('/integrations/linkedin/login-start', {
        email: loginEmail.trim(),
        password: loginPassword.trim(),
      })
      if (data.status === 'success') {
        setLoginMessage('Login successful! Session cookies saved.')
        setIntegrations((prev) =>
          prev ? { ...prev, linkedin: { ...prev.linkedin, has_session_cookies: true } } : prev,
        )
        setShowLoginForm(false)
        setLoginEmail('')
        setLoginPassword('')
      } else if (data.status === 'needs_verification') {
        setLoginSessionId(data.session_id)
        setLoginMessage('LinkedIn requires verification. Check your email and enter the code below.')
      } else if (data.status === 'captcha') {
        setLoginMessage('LinkedIn is showing a CAPTCHA. Please use the cookie method instead.')
      } else {
        setLoginMessage(data.error || 'Login failed. Try the cookie method instead.')
      }
    } catch {
      setLoginMessage('Login failed. Please try the cookie paste method instead.')
    } finally {
      setLoginLoading(false)
    }
  }

  const handleLoginVerify = async () => {
    if (!verificationCode.trim() || !loginSessionId) return
    setLoginLoading(true)
    try {
      const { data } = await api.post('/integrations/linkedin/login-verify', {
        session_id: loginSessionId,
        code: verificationCode.trim(),
      })
      if (data.status === 'success') {
        setLoginMessage('Verification successful! Session cookies saved.')
        setIntegrations((prev) =>
          prev ? { ...prev, linkedin: { ...prev.linkedin, has_session_cookies: true } } : prev,
        )
        setShowLoginForm(false)
        setLoginSessionId(null)
        setVerificationCode('')
        setLoginEmail('')
        setLoginPassword('')
      } else {
        setLoginMessage(data.error || 'Verification failed.')
      }
    } catch {
      setLoginMessage('Verification failed.')
    } finally {
      setLoginLoading(false)
    }
  }

  const handleSaveSettings = async () => {
    setSaving(true)
    setSaveMessage('')
    try {
      await api.put('/automation/settings', settings)
      setSaveMessage('Settings saved')
      setTimeout(() => setSaveMessage(''), 3000)
    } catch (err) {
      console.error('Failed to save settings:', err)
      setSaveMessage('Failed to save')
    } finally {
      setSaving(false)
    }
  }

  const handleAddPhrase = async () => {
    if (!newPhrase.trim()) return
    setAddingPhrase(true)
    try {
      const { data } = await api.post('/automation/avoid-phrases', { phrase: newPhrase.trim() })
      setAvoidPhrases((prev) => [data, ...prev])
      setNewPhrase('')
    } catch (err: unknown) {
      const error = err as { response?: { data?: { detail?: string } } }
      console.error('Failed to add phrase:', error.response?.data?.detail || 'Unknown error')
    } finally {
      setAddingPhrase(false)
    }
  }

  const handleDeletePhrase = async (id: string) => {
    try {
      await api.delete(`/automation/avoid-phrases/${id}`)
      setAvoidPhrases((prev) => prev.filter((p) => p.id !== id))
    } catch (err) {
      console.error('Failed to delete phrase:', err)
    }
  }

  const BOOKMARKLET = `javascript:void(document.cookie.split(';').forEach(c=>{if(c.trim().startsWith('li_at=')){prompt('Copy this li_at value:',c.trim().split('=').slice(1).join('='))}}))`;

  if (loading) {
    return <SectionLoading />
  }

  return (
    <div className="space-y-8 max-w-3xl">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Settings</h1>
        <p className="text-gray-500 mt-1">Manage integrations, automation preferences, and writing rules</p>
      </div>

      {/* LinkedIn Integration */}
      <section className="bg-white rounded-xl shadow-sm p-6 space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-lg font-semibold">LinkedIn</h2>
            <p className="text-sm text-gray-500">Connect your LinkedIn account for automated engagement</p>
          </div>
          <StatusBadge connected={integrations?.linkedin.connected || false} />
        </div>
        {!integrations?.linkedin.connected && (
          <button
            onClick={handleConnectLinkedIn}
            disabled={connectingLinkedIn}
            className="px-6 py-2.5 bg-[#0077b5] text-white rounded-lg text-sm font-medium hover:bg-[#005885] disabled:opacity-50 transition-colors"
          >
            {connectingLinkedIn ? 'Redirecting...' : 'Connect LinkedIn'}
          </button>
        )}
        {integrations?.linkedin.connected && (
          <div className="border-t pt-4 space-y-4">
            {/* Session status row */}
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <h3 className="text-sm font-medium text-gray-700">Browser Session</h3>
                {integrations.linkedin.has_session_cookies ? (
                  <span className="px-2 py-0.5 rounded text-xs font-medium bg-green-100 text-green-700">Active</span>
                ) : (
                  <span className="px-2 py-0.5 rounded text-xs font-medium bg-yellow-100 text-yellow-700">Not configured</span>
                )}
              </div>
              {/* Connected user name */}
              {integrations.linkedin.user_name && (
                <span className="text-xs text-gray-500">
                  Connected as <span className="font-medium text-gray-700">{integrations.linkedin.user_name}</span>
                </span>
              )}
            </div>
            <p className="text-xs text-gray-500">
              Required for post discovery and browser-based engagement. Cookie typically lasts ~1 year.
            </p>

            {/* Method 1: Cookie paste with guided walkthrough */}
            <div className="space-y-2">
              <button
                onClick={() => setShowCookieGuide(!showCookieGuide)}
                className="text-sm text-primary-600 hover:text-primary-700 font-medium"
              >
                {showCookieGuide ? 'Hide instructions' : integrations.linkedin.has_session_cookies ? 'Re-connect session' : 'Option 1: Paste cookie manually'}
              </button>
              {showCookieGuide && (
                <div className="bg-gray-50 rounded-lg p-4 space-y-3">
                  <p className="text-xs font-medium text-gray-700">Step-by-step:</p>
                  <ol className="text-xs text-gray-600 space-y-1.5 list-decimal list-inside">
                    <li>Open <strong>linkedin.com</strong> in Chrome and make sure you are logged in</li>
                    <li>Press <strong>F12</strong> (or right-click &rarr; Inspect) to open Developer Tools</li>
                    <li>Click the <strong>Application</strong> tab at the top of DevTools</li>
                    <li>In the left sidebar, expand <strong>Cookies</strong> &rarr; click <strong>linkedin.com</strong></li>
                    <li>Find <code className="bg-white px-1 py-0.5 rounded border text-xs">li_at</code> in the list and double-click the <strong>Value</strong> column</li>
                    <li>Copy the value (Ctrl+C / Cmd+C) and paste it below</li>
                  </ol>
                  <p className="text-xs text-gray-500">
                    Or use this bookmarklet: drag{' '}
                    <a
                      href={BOOKMARKLET}
                      onClick={(e) => e.preventDefault()}
                      className="px-2 py-0.5 bg-primary-100 text-primary-700 rounded text-xs font-medium cursor-grab"
                      title="Drag this to your bookmarks bar, then click it while on linkedin.com"
                    >
                      Extract li_at
                    </a>{' '}
                    to your bookmarks bar, then click it while on LinkedIn.
                  </p>
                  <div className="flex gap-2">
                    <input
                      type="password"
                      value={liAtCookie}
                      onChange={(e) => setLiAtCookie(e.target.value)}
                      placeholder="Paste li_at cookie value"
                      className="flex-1 px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary-500"
                    />
                    <button
                      onClick={handleSaveSession}
                      disabled={savingSession || !liAtCookie.trim()}
                      className="px-4 py-2 bg-[#0077b5] text-white rounded-lg text-sm font-medium hover:bg-[#005885] disabled:opacity-50 transition-colors whitespace-nowrap"
                    >
                      {savingSession ? 'Validating...' : 'Save Session'}
                    </button>
                  </div>
                </div>
              )}
            </div>

            {/* Method 2: In-app login */}
            <div className="space-y-2">
              <button
                onClick={() => setShowLoginForm(!showLoginForm)}
                className="text-sm text-primary-600 hover:text-primary-700 font-medium"
              >
                {showLoginForm ? 'Hide login form' : 'Option 2: Login with LinkedIn credentials'}
              </button>
              {showLoginForm && (
                <div className="bg-gray-50 rounded-lg p-4 space-y-3">
                  <p className="text-xs text-gray-500">
                    Enter your LinkedIn email and password. Your credentials are <strong>not stored</strong> — we only
                    save the resulting session cookie.
                  </p>
                  {!loginSessionId ? (
                    <div className="space-y-2">
                      <input
                        type="email"
                        value={loginEmail}
                        onChange={(e) => setLoginEmail(e.target.value)}
                        placeholder="LinkedIn email"
                        className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary-500"
                      />
                      <input
                        type="password"
                        value={loginPassword}
                        onChange={(e) => setLoginPassword(e.target.value)}
                        placeholder="LinkedIn password"
                        className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary-500"
                      />
                      <button
                        onClick={handleLoginStart}
                        disabled={loginLoading || !loginEmail.trim() || !loginPassword.trim()}
                        className="px-4 py-2 bg-[#0077b5] text-white rounded-lg text-sm font-medium hover:bg-[#005885] disabled:opacity-50 transition-colors"
                      >
                        {loginLoading ? 'Logging in...' : 'Login'}
                      </button>
                    </div>
                  ) : (
                    <div className="space-y-2">
                      <p className="text-xs text-amber-600 font-medium">Verification required</p>
                      <input
                        type="text"
                        value={verificationCode}
                        onChange={(e) => setVerificationCode(e.target.value)}
                        placeholder="Enter verification code"
                        className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary-500"
                      />
                      <button
                        onClick={handleLoginVerify}
                        disabled={loginLoading || !verificationCode.trim()}
                        className="px-4 py-2 bg-[#0077b5] text-white rounded-lg text-sm font-medium hover:bg-[#005885] disabled:opacity-50 transition-colors"
                      >
                        {loginLoading ? 'Verifying...' : 'Submit Code'}
                      </button>
                    </div>
                  )}
                </div>
              )}
            </div>

            {/* Session messages */}
            {(sessionMessage || loginMessage) && (
              <p
                className={`text-xs ${
                  (sessionMessage || loginMessage).includes('saved') || (sessionMessage || loginMessage).includes('successful')
                    ? 'text-green-600'
                    : 'text-red-600'
                }`}
              >
                {sessionMessage || loginMessage}
              </p>
            )}
          </div>
        )}
      </section>

      {/* Meta (Instagram/Facebook) Integration */}
      <section className="bg-white rounded-xl shadow-sm p-6 space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-lg font-semibold">Instagram & Facebook</h2>
            <p className="text-sm text-gray-500">Connect your Meta account for Instagram and Facebook engagement</p>
          </div>
          <StatusBadge connected={integrations?.meta.connected || false} />
        </div>
        {integrations?.meta.connected ? (
          <p className="text-sm text-green-600">Meta account connected. Instagram and Facebook engagement is active.</p>
        ) : (
          <button
            onClick={handleConnectMeta}
            disabled={connectingMeta}
            className="px-6 py-2.5 bg-gradient-to-r from-[#833AB4] via-[#E1306C] to-[#F77737] text-white rounded-lg text-sm font-medium hover:opacity-90 disabled:opacity-50 transition-opacity"
          >
            {connectingMeta ? 'Redirecting...' : 'Connect Instagram & Facebook'}
          </button>
        )}
      </section>

      {/* WhatsApp Integration */}
      <section className="bg-white rounded-xl shadow-sm p-6 space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-lg font-semibold">WhatsApp Group Monitor</h2>
            <p className="text-sm text-gray-500">Monitor WhatsApp groups for shared social media links</p>
          </div>
          <StatusBadge connected={integrations?.whatsapp.connected || false} />
        </div>

        {integrations?.whatsapp.connected ? (
          <p className="text-sm text-green-600">WhatsApp is connected and monitoring groups.</p>
        ) : whatsappStatus === 'unavailable' ? (
          <p className="text-sm text-gray-600">
            WhatsApp sidecar is not running. Start it with <code className="bg-gray-100 px-1.5 py-0.5 rounded text-xs">docker compose up whatsapp-sidecar</code>
          </p>
        ) : qrCode ? (
          <div>
            <p className="text-sm font-medium text-gray-700 mb-3">Scan this QR code with WhatsApp:</p>
            <img src={qrCode} alt="WhatsApp QR Code" className="w-64 h-64 border rounded-lg" />
          </div>
        ) : (
          <p className="text-sm text-gray-500">Initializing WhatsApp connection...</p>
        )}
      </section>

      {/* Automation Preferences */}
      <section className="bg-white rounded-xl shadow-sm p-6 space-y-6">
        <h2 className="text-lg font-semibold">Automation Preferences</h2>

        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">Risk Profile</label>
            <div className="flex gap-3">
              <label className="flex items-center gap-2 px-4 py-3 border rounded-lg cursor-pointer hover:bg-gray-50">
                <input
                  type="radio"
                  name="risk"
                  value="safe"
                  checked={settings.risk_profile === 'safe'}
                  onChange={() => setSettings({ ...settings, risk_profile: 'safe' })}
                  className="text-primary-600"
                />
                <div>
                  <p className="text-sm font-medium">Safe Mode</p>
                  <p className="text-xs text-gray-500">Longer delays, daily caps (50 likes, 20 comments), weekend dampening</p>
                </div>
              </label>
              <label className="flex items-center gap-2 px-4 py-3 border rounded-lg cursor-pointer hover:bg-gray-50">
                <input
                  type="radio"
                  name="risk"
                  value="aggro"
                  checked={settings.risk_profile === 'aggro'}
                  onChange={() => setSettings({ ...settings, risk_profile: 'aggro' })}
                  className="text-primary-600"
                />
                <div>
                  <p className="text-sm font-medium">Aggro Mode</p>
                  <p className="text-xs text-gray-500">Minimal delays, higher caps (150 likes, 60 comments), no weekend dampening</p>
                </div>
              </label>
            </div>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">Polling Interval</label>
            <select
              value={settings.polling_interval}
              onChange={(e) => setSettings({ ...settings, polling_interval: Number(e.target.value) })}
              className="px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-primary-500"
            >
              <option value={300}>Every 5 minutes</option>
              <option value={600}>Every 10 minutes</option>
              <option value={900}>Every 15 minutes</option>
            </select>
          </div>

          <div>
            <div className="flex items-center justify-between mb-2">
              <label className="block text-sm font-medium text-gray-700">Quiet Hours</label>
              <button
                type="button"
                onClick={() => setSettings({ ...settings, quiet_hours_enabled: !settings.quiet_hours_enabled })}
                className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors focus:outline-none ${
                  settings.quiet_hours_enabled ? 'bg-primary-600' : 'bg-gray-200'
                }`}
              >
                <span
                  className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform ${
                    settings.quiet_hours_enabled ? 'translate-x-6' : 'translate-x-1'
                  }`}
                />
              </button>
            </div>
            <div className={`flex items-center gap-3 ${!settings.quiet_hours_enabled ? 'opacity-40 pointer-events-none' : ''}`}>
              <input
                type="time"
                value={settings.quiet_hours_start}
                onChange={(e) => setSettings({ ...settings, quiet_hours_start: e.target.value })}
                className="px-3 py-2 border border-gray-300 rounded-lg shadow-sm"
              />
              <span className="text-gray-500">to</span>
              <input
                type="time"
                value={settings.quiet_hours_end}
                onChange={(e) => setSettings({ ...settings, quiet_hours_end: e.target.value })}
                className="px-3 py-2 border border-gray-300 rounded-lg shadow-sm"
              />
            </div>
            <p className="text-xs text-gray-500 mt-1">
              {settings.quiet_hours_enabled
                ? 'Engagements will be deferred until quiet hours end'
                : 'Quiet hours disabled — engagements run immediately, 24/7'}
            </p>
          </div>
        </div>

        <div className="flex items-center gap-3 pt-2">
          <button
            onClick={handleSaveSettings}
            disabled={saving}
            className="px-6 py-2.5 bg-primary-600 text-white rounded-lg text-sm font-medium hover:bg-primary-700 disabled:opacity-50 transition-colors"
          >
            {saving ? 'Saving...' : 'Save Settings'}
          </button>
          {saveMessage && (
            <span className={`text-sm ${saveMessage === 'Settings saved' ? 'text-green-600' : 'text-red-600'}`}>
              {saveMessage}
            </span>
          )}
        </div>
      </section>

      {/* Writing Rules (Avoid Phrases) */}
      <section className="bg-white rounded-xl shadow-sm p-6 space-y-4">
        <div>
          <h2 className="text-lg font-semibold">Writing Rules</h2>
          <p className="text-sm text-gray-500 mt-1">
            Custom phrases and patterns the AI should never use in generated comments.
            Em dashes and common generic phrases are blocked by default.
          </p>
        </div>

        <div className="flex gap-2">
          <input
            type="text"
            value={newPhrase}
            onChange={(e) => setNewPhrase(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleAddPhrase()}
            placeholder='e.g. "game changer", "no emojis", "always ask a question"'
            className="flex-1 px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary-500"
          />
          <button
            onClick={handleAddPhrase}
            disabled={addingPhrase || !newPhrase.trim()}
            className="px-4 py-2 bg-primary-600 text-white rounded-lg text-sm font-medium hover:bg-primary-700 disabled:opacity-50 transition-colors whitespace-nowrap"
          >
            {addingPhrase ? 'Adding...' : 'Add Rule'}
          </button>
        </div>

        {avoidPhrases.length > 0 && (
          <div className="flex flex-wrap gap-2">
            {avoidPhrases.map((phrase) => (
              <span
                key={phrase.id}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-red-50 text-red-700 rounded-lg text-sm"
              >
                {phrase.phrase}
                <button
                  onClick={() => handleDeletePhrase(phrase.id)}
                  className="text-red-400 hover:text-red-600 font-bold"
                  title="Remove"
                >
                  &times;
                </button>
              </span>
            ))}
          </div>
        )}

        <p className="text-xs text-gray-400">
          Built-in rules: no em/en dashes, no &quot;thanks for sharing&quot;, no &quot;great insights&quot;, no &quot;couldn&apos;t agree more&quot;, and 15+ other generic AI phrases.
        </p>
      </section>
    </div>
  )
}
