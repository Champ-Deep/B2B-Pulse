interface BadgeProps {
  className?: string
}

export function PlatformBadge({ platform, pageType }: { platform: string; pageType: string } & BadgeProps) {
  const config: Record<string, { color: string; label: string }> = {
    linkedin: { color: 'bg-blue-100 text-blue-700', label: 'LinkedIn' },
    meta: {
      color: 'bg-pink-100 text-pink-700',
      label: pageType === 'ig_business' ? 'Instagram' : pageType === 'fb_page' ? 'Facebook' : 'Meta',
    },
  }
  const { color, label } = config[platform] || { color: 'bg-gray-100 text-gray-600', label: platform }
  return (
    <span className={`px-2 py-0.5 rounded text-xs font-medium ${color}`}>
      {label}
    </span>
  )
}

export function RoleBadge({ role }: { role: string }) {
  const colors: Record<string, string> = {
    owner: 'bg-purple-100 text-purple-700',
    admin: 'bg-blue-100 text-blue-700',
    member: 'bg-gray-100 text-gray-700',
    analyst: 'bg-green-100 text-green-700',
  }
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${colors[role] || colors.member}`}>
      {role}
    </span>
  )
}

export function ConnectionBadge({ platform, connected }: { platform: string; connected: boolean }) {
  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${
        connected ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-400'
      }`}
    >
      {platform}
    </span>
  )
}

export function StatusBadge({ connected }: { connected: boolean }) {
  return (
    <span className={`px-3 py-1 rounded-full text-xs font-medium ${
      connected ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-500'
    }`}>
      {connected ? 'Connected' : 'Not connected'}
    </span>
  )
}

export function ActionStatusBadge({ action }: { action: string }) {
  let color = 'bg-gray-100 text-gray-600'
  if (action.includes('completed')) color = 'bg-green-100 text-green-700'
  else if (action.includes('failed')) color = 'bg-red-100 text-red-700'
  else if (action.includes('pending')) color = 'bg-yellow-100 text-yellow-700'
  return (
    <span className={`px-2.5 py-1 rounded-full text-xs font-medium ${color}`}>
      {action}
    </span>
  )
}
