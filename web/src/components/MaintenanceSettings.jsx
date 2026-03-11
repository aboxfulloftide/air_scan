import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import api from '../api/client'
import { Trash2, Play, Loader2, Check } from 'lucide-react'

const RETENTION_OPTIONS = [1, 2, 3, 7, 14, 30, 60, 90]

export default function MaintenanceSettings() {
  const queryClient = useQueryClient()
  const [cleaning, setCleaning] = useState(false)
  const [lastResult, setLastResult] = useState(null)

  const { data: settings } = useQuery({
    queryKey: ['maintenance-settings'],
    queryFn: () => api.get('/maintenance/settings').then((r) => r.data),
  })

  const { data: obsCount } = useQuery({
    queryKey: ['obs-count'],
    queryFn: () => api.get('/dashboard/').then((r) => r.data),
    select: (d) => d.total_devices,  // just for a live feel — replace if there's a better endpoint
  })

  const retentionDays = parseInt(settings?.observation_retention_days ?? 3)
  const lastCleanup = settings?.last_cleanup_at
  const lastDeleted = settings?.last_cleanup_deleted

  const updateRetention = useMutation({
    mutationFn: (days) => api.patch('/maintenance/settings', { observation_retention_days: days }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['maintenance-settings'] }),
  })

  const runCleanup = async () => {
    setCleaning(true)
    setLastResult(null)
    try {
      const res = await api.post('/maintenance/cleanup')
      setLastResult(res.data)
      queryClient.invalidateQueries({ queryKey: ['maintenance-settings'] })
    } finally {
      setCleaning(false)
    }
  }

  return (
    <div className="space-y-4">
      <h3 className="text-lg font-semibold text-white flex items-center gap-2">
        <Trash2 className="w-5 h-5 text-gray-400" /> DB Maintenance
      </h3>

      <div className="bg-gray-900 border border-gray-800 rounded-lg p-5 space-y-4">
        <div className="flex items-center justify-between gap-4">
          <div>
            <p className="text-sm text-white font-medium">Observation Retention</p>
            <p className="text-xs text-gray-500 mt-0.5">Older observations are deleted by the nightly cleanup (3:00 AM)</p>
          </div>
          <select
            value={retentionDays}
            onChange={(e) => updateRetention.mutate(parseInt(e.target.value))}
            className="bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-sm text-white focus:outline-none focus:border-blue-500 shrink-0"
          >
            {RETENTION_OPTIONS.map((d) => (
              <option key={d} value={d}>{d} {d === 1 ? 'day' : 'days'}</option>
            ))}
          </select>
        </div>

        <div className="flex items-center justify-between border-t border-gray-800 pt-4">
          <div className="text-xs text-gray-500 space-y-0.5">
            {lastCleanup && (
              <p>Last run: <span className="text-gray-400">{new Date(lastCleanup).toLocaleString()}</span>
                {lastDeleted !== undefined && <span className="text-gray-500"> — {parseInt(lastDeleted).toLocaleString()} rows deleted</span>}
              </p>
            )}
            {lastResult && (
              <p className="text-green-400">
                <Check className="w-3 h-3 inline mr-1" />
                Deleted {lastResult.deleted.toLocaleString()} rows older than {lastResult.retention_days} days
              </p>
            )}
          </div>
          <button
            onClick={runCleanup}
            disabled={cleaning}
            className="px-3 py-1.5 bg-red-700 hover:bg-red-600 disabled:opacity-40 rounded text-sm text-white flex items-center gap-1.5 shrink-0"
          >
            {cleaning ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
            Run Now
          </button>
        </div>
      </div>
    </div>
  )
}
