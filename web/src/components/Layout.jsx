import { Outlet, NavLink } from 'react-router-dom'
import { LayoutDashboard, Radio, Map, Wifi, Navigation, Crosshair } from 'lucide-react'

const navItems = [
  { to: '/dashboard', label: 'Dashboard', icon: LayoutDashboard },
  { to: '/devices', label: 'Devices', icon: Wifi },
  { to: '/map', label: 'Map', icon: Map },
  { to: '/mobile', label: 'Mobile', icon: Navigation },
  { to: '/calibrate', label: 'Calibrate', icon: Crosshair },
  { to: '/scanners', label: 'Scanners', icon: Radio },
]

export default function Layout() {
  return (
    <div className="flex h-screen">
      <nav className="w-56 bg-gray-900 border-r border-gray-800 flex flex-col">
        <div className="px-4 py-5 border-b border-gray-800">
          <h1 className="text-lg font-bold text-white flex items-center gap-2">
            <Wifi className="w-5 h-5 text-blue-400" />
            Air Scan
          </h1>
        </div>
        <div className="flex-1 py-3">
          {navItems.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                `flex items-center gap-3 px-4 py-2.5 text-sm transition-colors ${
                  isActive
                    ? 'bg-gray-800 text-white border-r-2 border-blue-400'
                    : 'text-gray-400 hover:text-white hover:bg-gray-800/50'
                }`
              }
            >
              <Icon className="w-4 h-4" />
              {label}
            </NavLink>
          ))}
        </div>
        <div className="px-4 py-3 border-t border-gray-800 text-xs text-gray-500">
          Air Scan v0.1
        </div>
      </nav>
      <main className="flex-1 overflow-auto bg-gray-950">
        <Outlet />
      </main>
    </div>
  )
}
