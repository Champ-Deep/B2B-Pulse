import { useEffect, useState } from 'react'
import api from '../api/client'
import { StatusBadge } from '../components/Badge'
import { SectionLoading } from '../components/Loading'
import type { AutomationSettings, IntegrationStatus } from '../lib/types'

export default function AutomationSettings() {
  const [integrations, setIntegrations] = useState<IntegrationStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [connectingLinkedIn, setConnectingLinkedIn] = useState(false)
  const [connectingMeta, setConnectingMeta] = useState(false)

  // Automation settings state
  const [settings, setSettings] = useState<AutomationSettings>({
    risk_profile: 'safe',
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

  // WhatsApp QR state
  const [qrCode, setQrCode] = useState<string | null>(null)
  const [whatsappStatus, setWhatsappStatus] = useState<string>('initializing')

  useEffect(() => {
    const fetchData = async () => {
      try {
        const [statusRes, settingsRes] = await Promise.all([
          api.get('/integrations/status'),
          api.get('/automation/settings'),
        ])
        setIntegrations(statusRes.data)
        setSettings(settingsRes.data)
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
        // Sidecar not available
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

  if (loading) {
    return <SectionLoading />
  }

  return (
    <div className="space-y-8 max-w-3xl">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Settings</h1>
        <p className="text-gray-500 mt-1">Manage integrations and automation preferences</p>
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
          <div className="border-t pt-4 space-y-3">
            <div className="flex items-center gap-2">
              <h3 className="text-sm font-medium text-gray-700">Browser Session</h3>
              {integrations.linkedin.has_session_cookies ? (
                <span className="px-2 py-0.5 rounded text-xs font-medium bg-green-100 text-green-700">Active</span>
              ) : (
                <span className="px-2 py-0.5 rounded text-xs font-medium bg-yellow-100 text-yellow-700">
                  Not configured
                </span>
              )}
            </div>
            <p className="text-xs text-gray-500">
              Required for post discovery. Open LinkedIn in your browser, press F12, go to Application &rarr; Cookies
              &rarr; linkedin.com, and copy the value of <code className="bg-gray-100 px-1 py-0.5 rounded">li_at</code>.
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
            {sessionMessage && (
              <p
                className={`text-xs ${sessionMessage.startsWith('Session saved') ? 'text-green-600' : 'text-red-600'}`}
              >
                {sessionMessage}
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
                  <p className="text-xs text-gray-500">Random delays 1-7s, lower daily limits</p>
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
                  <p className="text-xs text-gray-500">Minimal delays, higher limits (internal pages)</p>
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
            <label className="block text-sm font-medium text-gray-700 mb-2">Quiet Hours</label>
            <div className="flex items-center gap-3">
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
            <p className="text-xs text-gray-500 mt-1">Actions will be queued during quiet hours and executed after</p>
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
    </div>
  )
}
