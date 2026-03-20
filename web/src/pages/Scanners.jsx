import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import api from '../api/client'
import { Radio, MapPin, Pencil, Check, X, Trash2 } from 'lucide-react'
import DeployTargets from '../components/DeployTargets'
import MaintenanceSettings from '../components/MaintenanceSettings'

const healthColor = {
  online: 'text-green-400',
  stale: 'text-yellow-400',
  offline: 'text-red-400',
}
const healthDot = {
  online: 'bg-green-400',
  stale: 'bg-yellow-400',
  offline: 'bg-red-400',
}

export default function Scanners() {
  const queryClient = useQueryClient()
  const [editing, setEditing] = useState(null)
  const [editLabel, setEditLabel] = useState('')

  const { data: scanners, isLoading } = useQuery({
    queryKey: ['scanners'],
    queryFn: () => api.get('/scanners/').then((r) => r.data),
    refetchInterval: 15000,
  })

  const updateScanner = useMutation({
    mutationFn: ({ id, ...body }) => api.patch(`/scanners/${id}`, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['scanners'] })
      setEditing(null)
    },
  })

  const deleteScanner = useMutation({
    mutationFn: (id) => api.delete(`/scanners/${id}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['scanners'] }),
  })

  const confirmDelete = (s) => {
    if (window.confirm(`Delete scanner "${s.label || s.hostname}"?`)) {
      deleteScanner.mutate(s.id)
    }
  }

  const startEdit = (s) => {
    setEditing(s.id)
    setEditLabel(s.label || '')
  }

  const saveLabel = (id) => {
    updateScanner.mutate({ id, label: editLabel })
  }

  if (isLoading) return <div className="p-6 text-gray-400">Loading...</div>

  return (
    <div className="p-6 space-y-4">
      <h2 className="text-xl font-bold text-white">Scanners</h2>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {(scanners || []).map((s) => (
          <div key={s.id} className="bg-gray-900 border border-gray-800 rounded-lg p-5 space-y-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <span className={`w-3 h-3 rounded-full ${healthDot[s.health]}`} />
                <div>
                  {editing === s.id ? (
                    <div className="flex items-center gap-2">
                      <input
                        type="text"
                        value={editLabel}
                        onChange={(e) => setEditLabel(e.target.value)}
                        className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-white focus:outline-none focus:border-blue-500"
                        autoFocus
                        onKeyDown={(e) => e.key === 'Enter' && saveLabel(s.id)}
                      />
                      <button onClick={() => saveLabel(s.id)} className="text-green-400 hover:text-green-300">
                        <Check className="w-4 h-4" />
                      </button>
                      <button onClick={() => setEditing(null)} className="text-gray-400 hover:text-white">
                        <X className="w-4 h-4" />
                      </button>
                    </div>
                  ) : (
                    <div className="flex items-center gap-2">
                      <h3 className="font-medium text-white">{s.label || s.hostname}</h3>
                      <button onClick={() => startEdit(s)} className="text-gray-500 hover:text-white">
                        <Pencil className="w-3 h-3" />
                      </button>
                    </div>
                  )}
                  <p className="text-xs text-gray-500 font-mono">{s.hostname}</p>
                </div>
              </div>
              <div className="flex items-center gap-2">
                <span className={`text-sm ${healthColor[s.health]}`}>{s.health}</span>
                {s.health === 'offline' && (
                  <button onClick={() => confirmDelete(s)} className="text-gray-600 hover:text-red-400 transition-colors" title="Delete scanner">
                    <Trash2 className="w-4 h-4" />
                  </button>
                )}
              </div>
            </div>

            <div className="grid grid-cols-2 gap-y-2 text-sm">
              <span className="text-gray-500">Last Heartbeat</span>
              <span className="text-gray-300">
                {s.last_heartbeat ? new Date(s.last_heartbeat + 'Z').toLocaleString() : 'Never'}
              </span>
              <span className="text-gray-500">Position</span>
              <span className="text-gray-300">
                {s.x_pos != null ? (
                  <span className="font-mono text-xs">
                    {parseFloat(s.x_pos).toFixed(5)}, {parseFloat(s.y_pos).toFixed(5)}
                  </span>
                ) : (
                  <Link to="/map" className="text-blue-400 hover:underline flex items-center gap-1">
                    <MapPin className="w-3 h-3" /> Set on map
                  </Link>
                )}
              </span>
              <span className="text-gray-500">Height</span>
              <span className="text-gray-300">{s.z_pos ? `${parseFloat(s.z_pos)} ft` : 'Ground level'}</span>
              <span className="text-gray-500">Obs (10m)</span>
              <span className="text-gray-300">{s.recent_obs ?? '-'}</span>
            </div>
          </div>
        ))}
      </div>

      {(!scanners || scanners.length === 0) && (
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-8 text-center">
          <Radio className="w-12 h-12 text-gray-600 mx-auto mb-3" />
          <p className="text-gray-400">No scanners registered yet.</p>
          <p className="text-sm text-gray-600 mt-1">Scanners self-register when they connect to the database.</p>
        </div>
      )}

      <hr className="border-gray-800" />

      <DeployTargets />

      <hr className="border-gray-800" />

      <MaintenanceSettings />
    </div>
  )
}
