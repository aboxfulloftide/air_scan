import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import api from '../api/client'
import { Wifi, Radio, Shield, ShieldAlert, Activity, Eye } from 'lucide-react'

function StatCard({ label, value, sub, icon: Icon, color = 'blue', to }) {
  const card = (
    <div className="bg-gray-900 border border-gray-800 rounded-lg p-5">
      <div className="flex items-center justify-between">
        <div>
          <p className="text-sm text-gray-400">{label}</p>
          <p className="text-2xl font-bold text-white mt-1">{value}</p>
          {sub && <p className="text-xs text-gray-500 mt-1">{sub}</p>}
        </div>
        <Icon className={`w-8 h-8 text-${color}-400 opacity-60`} />
      </div>
    </div>
  )
  return to ? <Link to={to}>{card}</Link> : card
}

function ScannerStatus({ scanners }) {
  if (!scanners?.length) return null

  const healthColor = { online: 'text-green-400', stale: 'text-yellow-400', offline: 'text-red-400' }
  const healthDot = { online: 'bg-green-400', stale: 'bg-yellow-400', offline: 'bg-red-400' }

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-lg p-5">
      <h3 className="text-sm font-medium text-gray-400 mb-3">Scanner Health</h3>
      <div className="space-y-2">
        {scanners.map((s) => (
          <div key={s.hostname} className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className={`w-2 h-2 rounded-full ${healthDot[s.health]}`} />
              <span className="text-sm text-white">{s.label || s.hostname}</span>
            </div>
            <span className={`text-xs ${healthColor[s.health]}`}>{s.health}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

export default function Dashboard() {
  const { data, isLoading } = useQuery({
    queryKey: ['dashboard'],
    queryFn: () => api.get('/dashboard/').then((r) => r.data),
    refetchInterval: 15000,
  })

  if (isLoading) {
    return <div className="p-6 text-gray-400">Loading...</div>
  }

  const d = data || {}
  const kb = d.known_breakdown || {}

  return (
    <div className="p-6 space-y-6">
      <h2 className="text-xl font-bold text-white">Dashboard</h2>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          label="Total Devices"
          value={d.total_devices}
          sub={`${d.total_aps} APs / ${d.total_clients} Clients`}
          icon={Wifi}
          to="/devices"
        />
        <StatCard
          label="Active (5m)"
          value={d.active_devices}
          sub={`${d.active_1h} in 1h / ${d.active_24h} in 24h`}
          icon={Activity}
          color="green"
        />
        <StatCard
          label="Known Devices"
          value={kb.known || 0}
          sub={`${kb.guest || 0} guest / ${kb.rogue || 0} rogue`}
          icon={Shield}
          color="emerald"
          to="/devices?status=known"
        />
        <StatCard
          label="Unknown Devices"
          value={kb.unknown || 0}
          sub={`${d.randomized_macs} randomized MACs`}
          icon={ShieldAlert}
          color="amber"
          to="/devices?status=unknown"
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <ScannerStatus scanners={d.scanners} />

        <div className="bg-gray-900 border border-gray-800 rounded-lg p-5">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-medium text-gray-400">Observation Rate</h3>
            <Eye className="w-4 h-4 text-gray-500" />
          </div>
          <p className="text-2xl font-bold text-white">{d.obs_per_minute}</p>
          <p className="text-xs text-gray-500">observations/min (10m avg)</p>
        </div>
      </div>

      {d.top_ssids?.length > 0 && (
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-5">
          <h3 className="text-sm font-medium text-gray-400 mb-3">Top SSIDs (24h)</h3>
          <div className="grid grid-cols-2 lg:grid-cols-5 gap-2">
            {d.top_ssids.map((s) => (
              <div key={s.ssid} className="flex items-center justify-between bg-gray-800 rounded px-3 py-2">
                <span className="text-sm text-white truncate">{s.ssid}</span>
                <span className="text-xs text-gray-400 ml-2">{s.device_count}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
