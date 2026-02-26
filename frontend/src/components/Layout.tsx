import { Outlet, NavLink } from 'react-router-dom'
import { Home, Archive, User, Settings, Users, ClipboardList, Shield } from 'lucide-react'
import { useAuth } from '../lib/auth'
import { ROUTES } from '../lib/routes'

const navItems = [
  { to: ROUTES.HOME, label: 'Dashboard', icon: Home },
  { to: ROUTES.TRACKED_PAGES, label: 'Tracked Pages', icon: Archive },
  { to: ROUTES.ONBOARDING, label: 'Profile & Style', icon: User },
  { to: ROUTES.SETTINGS, label: 'Settings', icon: Settings },
  { to: ROUTES.TEAM, label: 'Team', icon: Users },
  { to: ROUTES.AUDIT, label: 'Audit Log', icon: ClipboardList },
]

export default function Layout() {
  const { user, logout } = useAuth()

  return (
    <div className="min-h-screen flex">
      {/* Sidebar */}
      <aside className="w-64 bg-gray-900 text-white flex flex-col">
        <div className="p-6">
          <h1 className="text-xl font-bold">B2B Pulse</h1>
          <p className="text-sm text-gray-400 mt-1">{user?.full_name}</p>
          {user?.role && (
            <span className="inline-block mt-1 text-xs px-2 py-0.5 rounded-full bg-gray-700 text-gray-300 capitalize">
              {user.role.replace('_', ' ')}
            </span>
          )}
        </div>

        <nav className="flex-1 px-4 space-y-1">
          {navItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === ROUTES.HOME}
              className={({ isActive }) =>
                `flex items-center px-3 py-2.5 rounded-lg text-sm font-medium transition-colors ${
                  isActive
                    ? 'bg-primary-600 text-white'
                    : 'text-gray-300 hover:bg-gray-800 hover:text-white'
                }`
              }
            >
              <item.icon className="w-5 h-5 mr-3 flex-shrink-0" />
              {item.label}
            </NavLink>
          ))}

          {/* Admin nav (platform admins only) */}
          {user?.is_platform_admin && (
            <NavLink
              to={ROUTES.ADMIN}
              className={({ isActive }) =>
                `flex items-center px-3 py-2.5 rounded-lg text-sm font-medium transition-colors ${
                  isActive
                    ? 'bg-amber-600 text-white'
                    : 'text-amber-300 hover:bg-gray-800 hover:text-amber-200'
                }`
              }
            >
              <Shield className="w-5 h-5 mr-3 flex-shrink-0" />
              Platform Admin
            </NavLink>
          )}
        </nav>

        <div className="p-4 border-t border-gray-700">
          <button
            onClick={logout}
            className="w-full px-3 py-2 text-sm text-gray-300 hover:bg-gray-800 hover:text-white rounded-lg transition-colors text-left"
          >
            Sign out
          </button>
        </div>
      </aside>

      {/* Main content */}
      <main className="flex-1 overflow-auto">
        <div className="p-8">
          <Outlet />
        </div>
      </main>
    </div>
  )
}
