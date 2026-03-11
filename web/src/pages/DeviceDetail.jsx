import { useParams } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import api from '../api/client'

const statusColors = {
  known: 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30',
  unknown: 'bg-gray-500/20 text-gray-400 border-gray-500/30',
  guest: 'bg-blue-500/20 text-blue-400 border-blue-500/30',
  rogue: 'bg-red-500/20 text-red-400 border-red-500/30',
}

export default function DeviceDetail() {
  const { mac } = useParams()
  const queryClient = useQueryClient()
  const [classifyOpen, setClassifyOpen] = useState(false)

  const { data, isLoading } = useQuery({
    queryKey: ['device', mac],
    queryFn: () => api.get(`/devices/${encodeURIComponent(mac)}`).then((r) => r.data),
    refetchInterval: 15000,
  })

  const classify = useMutation({
    mutationFn: (body) => api.patch(`/devices/${encodeURIComponent(mac)}/classify`, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['device', mac] })
      setClassifyOpen(false)
    },
  })

  if (isLoading) return <div className="p-6 text-gray-400">Loading...</div>
  if (!data?.device) return <div className="p-6 text-red-400">Device not found</div>

  const d = data.device
  const caps = [d.he_capable && 'WiFi 6 (HE)', d.vht_capable && 'WiFi 5 (VHT)', d.ht_capable && 'WiFi 4 (HT)'].filter(Boolean)

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold text-white font-mono">{mac}</h2>
          {d.known_label && <p className="text-gray-400">{d.known_label}</p>}
        </div>
        <button
          onClick={() => setClassifyOpen(!classifyOpen)}
          className="bg-gray-800 text-white text-sm px-4 py-2 rounded-lg hover:bg-gray-700"
        >
          Classify
        </button>
      </div>

      {classifyOpen && (
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-4 flex gap-2">
          {['known', 'unknown', 'guest', 'rogue'].map((s) => (
            <button
              key={s}
              onClick={() => classify.mutate({ status: s })}
              className={`px-4 py-2 rounded-lg text-sm border ${statusColors[s]} hover:opacity-80`}
            >
              {s}
            </button>
          ))}
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Info */}
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-5 space-y-3">
          <h3 className="text-sm font-medium text-gray-400">Device Info</h3>
          <div className="grid grid-cols-2 gap-y-2 text-sm">
            <span className="text-gray-500">Type</span>
            <span className="text-white">{d.device_type}</span>
            <span className="text-gray-500">Manufacturer</span>
            <span className="text-white">{d.manufacturer || '-'}</span>
            <span className="text-gray-500">OUI</span>
            <span className="text-white font-mono">{d.oui || '-'}</span>
            <span className="text-gray-500">Randomized MAC</span>
            <span className="text-white">{d.is_randomized ? 'Yes' : 'No'}</span>
            <span className="text-gray-500">Status</span>
            <span className={`inline-block text-xs px-2 py-0.5 rounded w-fit ${statusColors[d.known_status] || statusColors.unknown}`}>
              {d.known_status || 'unknown'}
            </span>
            <span className="text-gray-500">Capabilities</span>
            <span className="text-white">{caps.join(', ') || 'None detected'}</span>
            <span className="text-gray-500">First Seen</span>
            <span className="text-white">{new Date(d.first_seen + 'Z').toLocaleString()}</span>
            <span className="text-gray-500">Last Seen</span>
            <span className="text-white">{new Date(d.last_seen + 'Z').toLocaleString()}</span>
            {d.port_scan_host_id && (
              <>
                <span className="text-gray-500">Port Scan Host</span>
                <span className="text-blue-400">#{d.port_scan_host_id}</span>
              </>
            )}
          </div>
        </div>

        {/* SSIDs */}
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-5 space-y-3">
          <h3 className="text-sm font-medium text-gray-400">SSIDs ({data.ssids?.length || 0})</h3>
          {data.ssids?.length ? (
            <div className="space-y-1 max-h-64 overflow-y-auto">
              {data.ssids.map((s) => (
                <div key={s.ssid} className="flex items-center justify-between bg-gray-800 rounded px-3 py-2">
                  <span className="text-sm text-white">{s.ssid}</span>
                  <span className="text-xs text-gray-500">{new Date(s.first_seen + 'Z').toLocaleDateString()}</span>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-sm text-gray-600">No SSIDs recorded</p>
          )}
        </div>
      </div>

      {/* Recent Observations */}
      <div className="bg-gray-900 border border-gray-800 rounded-lg p-5 space-y-3">
        <h3 className="text-sm font-medium text-gray-400">Recent Observations (1h)</h3>
        {data.observations?.length ? (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-gray-500 border-b border-gray-800">
                  <th className="text-left px-3 py-2">Scanner</th>
                  <th className="text-left px-3 py-2">Signal</th>
                  <th className="text-left px-3 py-2">Channel</th>
                  <th className="text-left px-3 py-2">Freq</th>
                  <th className="text-left px-3 py-2">Time</th>
                </tr>
              </thead>
              <tbody>
                {data.observations.map((o, i) => (
                  <tr key={i} className="border-b border-gray-800/30">
                    <td className="px-3 py-2 text-white">{o.scanner_host}</td>
                    <td className="px-3 py-2">
                      <span className={`${o.signal_dbm > -50 ? 'text-green-400' : o.signal_dbm > -70 ? 'text-yellow-400' : 'text-red-400'}`}>
                        {o.signal_dbm} dBm
                      </span>
                    </td>
                    <td className="px-3 py-2 text-gray-300">{o.channel || '-'}</td>
                    <td className="px-3 py-2 text-gray-400">{o.freq_mhz ? `${o.freq_mhz} MHz` : '-'}</td>
                    <td className="px-3 py-2 text-gray-400">{new Date(o.recorded_at + 'Z').toLocaleTimeString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-sm text-gray-600">No recent observations</p>
        )}
      </div>
    </div>
  )
}
