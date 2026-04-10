import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import api from '../api/client'
import { Wifi, Radio, Shield, ShieldAlert, Activity, Eye, ChevronDown, ChevronUp } from 'lucide-react'

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

function ScannerDetail({ hostname }) {
  const { data, isLoading } = useQuery({
    queryKey: ['scannerStats', hostname],
    queryFn: () => api.get(`/scanners/stats/${encodeURIComponent(hostname)}`).then((r) => r.data).catch(() => null),
    refetchInterval: 15000,
  })

  if (isLoading) return <div className="px-3 py-2 text-xs text-gray-500">Loading...</div>
  if (!data) return <div className="px-3 py-2 text-xs text-gray-500">No data</div>

  const sigColor = (dbm) => {
    if (dbm == null) return 'text-gray-500'
    if (dbm >= -50) return 'text-green-400'
    if (dbm >= -65) return 'text-blue-400'
    if (dbm >= -75) return 'text-yellow-400'
    return 'text-red-400'
  }

  return (
    <div className="mt-2 space-y-3">
      {/* Summary stats */}
      <div className="grid grid-cols-4 gap-2">
        <div className="bg-gray-800 rounded px-2.5 py-1.5 text-center">
          <p className="text-lg font-bold text-white">{data.device_count || 0}</p>
          <p className="text-xs text-gray-500">Devices</p>
        </div>
        <div className="bg-gray-800 rounded px-2.5 py-1.5 text-center">
          <p className="text-lg font-bold text-white">{data.obs_count || 0}</p>
          <p className="text-xs text-gray-500">Obs (10m)</p>
        </div>
        <div className="bg-gray-800 rounded px-2.5 py-1.5 text-center">
          <p className="text-lg font-bold text-purple-400">{data.ap_count || 0}</p>
          <p className="text-xs text-gray-500">APs</p>
        </div>
        <div className="bg-gray-800 rounded px-2.5 py-1.5 text-center">
          <p className="text-lg font-bold text-blue-400">{data.client_count || 0}</p>
          <p className="text-xs text-gray-500">Clients</p>
        </div>
      </div>

      {/* Signal range */}
      {data.avg_signal != null && (
        <div className="flex items-center gap-3 text-xs">
          <span className="text-gray-500">Signal:</span>
          <span className={sigColor(data.max_signal)}>best {data.max_signal}</span>
          <span className={sigColor(data.avg_signal)}>avg {parseFloat(data.avg_signal).toFixed(0)}</span>
          <span className={sigColor(data.min_signal)}>worst {data.min_signal}</span>
          <span className="text-gray-600">dBm</span>
        </div>
      )}

      {/* Channel distribution */}
      {data.channels?.length > 0 && (
        <div>
          <p className="text-xs text-gray-500 mb-1">Channels</p>
          <div className="flex flex-wrap gap-1">
            {data.channels.slice(0, 12).map((ch) => (
              <span key={`${ch.channel}-${ch.freq_mhz}`}
                className="bg-gray-800 rounded px-2 py-0.5 text-xs text-gray-300">
                ch{ch.channel} <span className="text-gray-500">({ch.device_count})</span>
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Top devices */}
      {data.top_devices?.length > 0 && (
        <div>
          <p className="text-xs text-gray-500 mb-1">Top devices (10m)</p>
          <div className="max-h-48 overflow-y-auto space-y-0.5">
            {data.top_devices.map((d) => (
              <Link key={d.mac} to={`/devices/${encodeURIComponent(d.mac)}`}
                className="flex items-center justify-between px-2 py-1 rounded hover:bg-gray-800 transition-colors">
                <div className="truncate mr-2">
                  <span className="text-sm text-white">{d.known_label || d.ssids || d.mac}</span>
                  {d.manufacturer && <span className="text-xs text-gray-500 ml-1.5">{d.manufacturer}</span>}
                </div>
                <div className="flex items-center gap-2 shrink-0 text-xs">
                  <span className="text-gray-500">{d.device_type}</span>
                  <span className={sigColor(d.best_signal)}>{d.best_signal} dBm</span>
                  <span className="text-gray-600">x{d.obs_count}</span>
                </div>
              </Link>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function ScannerStatus({ scanners }) {
  const [expanded, setExpanded] = useState(null)

  if (!scanners?.length) return null

  const healthColor = { online: 'text-green-400', stale: 'text-yellow-400', offline: 'text-red-400' }
  const healthDot = { online: 'bg-green-400', stale: 'bg-yellow-400', offline: 'bg-red-400' }

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-lg p-5">
      <h3 className="text-sm font-medium text-gray-400 mb-3">Scanner Health</h3>
      <div className="space-y-1">
        {scanners.map((s) => (
          <div key={s.hostname}>
            <button
              onClick={() => setExpanded(expanded === s.hostname ? null : s.hostname)}
              className="w-full flex items-center justify-between px-2 py-1.5 rounded hover:bg-gray-800 transition-colors">
              <div className="flex items-center gap-2">
                <span className={`w-2 h-2 rounded-full ${healthDot[s.health]}`} />
                <span className="text-sm text-white">{s.label || s.hostname}</span>
              </div>
              <div className="flex items-center gap-2">
                <span className={`text-xs ${healthColor[s.health]}`}>{s.health}</span>
                {expanded === s.hostname
                  ? <ChevronUp className="w-3.5 h-3.5 text-gray-500" />
                  : <ChevronDown className="w-3.5 h-3.5 text-gray-500" />}
              </div>
            </button>
            {expanded === s.hostname && <ScannerDetail hostname={s.hostname} />}
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
