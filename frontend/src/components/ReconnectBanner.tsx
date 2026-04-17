import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import api from '../api/client'
import { ROUTES } from '../lib/routes'

type SessionStatus = {
  has_session_cookies: boolean
  needs_reconnect?: boolean
  is_active?: boolean
  source?: string | null
}

export default function ReconnectBanner() {
  const [status, setStatus] = useState<SessionStatus | null>(null)

  useEffect(() => {
    let cancelled = false
    const fetchIt = async () => {
      try {
        const { data } = await api.get<SessionStatus>('/integrations/linkedin/session-status')
        if (!cancelled) setStatus(data)
      } catch {
        /* silent */
      }
    }
    fetchIt()
    const id = window.setInterval(fetchIt, 60_000)
    return () => {
      cancelled = true
      window.clearInterval(id)
    }
  }, [])

  if (!status) return null
  const showBanner = status.needs_reconnect || (status.has_session_cookies && status.is_active === false)
  if (!showBanner) return null

  return (
    <div className="bg-amber-50 border-b border-amber-200 px-6 py-3 flex items-center justify-between gap-4">
      <div className="text-sm text-amber-900">
        <strong>LinkedIn session needs reconnecting.</strong>{' '}
        Automation is paused until we have a fresh session.
      </div>
      <Link
        to={ROUTES.ONBOARDING_LINKEDIN}
        className="px-3 py-1.5 bg-amber-600 text-white text-sm rounded-md hover:bg-amber-700 whitespace-nowrap"
      >
        Reconnect
      </Link>
    </div>
  )
}
