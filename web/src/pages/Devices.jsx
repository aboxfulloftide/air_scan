import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useSearchParams, Link } from 'react-router-dom'
import api from '../api/client'
import { Search, ChevronLeft, ChevronRight } from 'lucide-react'

const statusColors = {
  known: 'bg-emerald-500/20 text-emerald-400',
  unknown: 'bg-gray-500/20 text-gray-400',
  guest: 'bg-blue-500/20 text-blue-400',
  rogue: 'bg-red-500/20 text-red-400',
}

function timeAgo(dateStr) {
  if (!dateStr) return 'never'
  const diff = Date.now() - new Date(dateStr + 'Z').getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  const days = Math.floor(hrs / 24)
  return `${days}d ago`
}

export default function Devices() {
  const [searchParams, setSearchParams] = useSearchParams()
  const [search, setSearch] = useState(searchParams.get('search') || '')

  const page = parseInt(searchParams.get('page') || '1')
  const deviceType = searchParams.get('type') || ''
  const status = searchParams.get('status') || ''

  const { data, isLoading } = useQuery({
    queryKey: ['devices', page, deviceType, status, searchParams.get('search')],
    queryFn: () =>
      api
        .get('/devices/', {
          params: {
            page,
            per_page: 50,
            device_type: deviceType || undefined,
            status: status || undefined,
            search: searchParams.get('search') || undefined,
          },
        })
        .then((r) => r.data),
    refetchInterval: 30000,
  })

  const handleSearch = (e) => {
    e.preventDefault()
    const p = new URLSearchParams(searchParams)
    if (search) p.set('search', search)
    else p.delete('search')
    p.set('page', '1')
    setSearchParams(p)
  }

  const setFilter = (key, value) => {
    const p = new URLSearchParams(searchParams)
    if (value) p.set(key, value)
    else p.delete(key)
    p.set('page', '1')
    setSearchParams(p)
  }

  const setPage = (n) => {
    const p = new URLSearchParams(searchParams)
    p.set('page', String(n))
    setSearchParams(p)
  }

  const devices = data?.devices || []
  const total = data?.total || 0
  const totalPages = Math.ceil(total / 50)

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-bold text-white">Devices</h2>
        <span className="text-sm text-gray-400">{total} total</span>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap gap-3 items-center">
        <form onSubmit={handleSearch} className="flex gap-2">
          <div className="relative">
            <Search className="absolute left-3 top-2.5 w-4 h-4 text-gray-500" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="MAC, manufacturer, SSID..."
              className="bg-gray-900 border border-gray-700 rounded-lg pl-9 pr-3 py-2 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-blue-500 w-72"
            />
          </div>
          <button type="submit" className="bg-blue-600 text-white text-sm px-4 py-2 rounded-lg hover:bg-blue-500">
            Search
          </button>
        </form>

        <select
          value={deviceType}
          onChange={(e) => setFilter('type', e.target.value)}
          className="bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white"
        >
          <option value="">All Types</option>
          <option value="AP">APs</option>
          <option value="Client">Clients</option>
        </select>

        <select
          value={status}
          onChange={(e) => setFilter('status', e.target.value)}
          className="bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white"
        >
          <option value="">All Status</option>
          <option value="known">Known</option>
          <option value="unknown">Unknown</option>
          <option value="guest">Guest</option>
          <option value="rogue">Rogue</option>
        </select>
      </div>

      {/* Table */}
      {isLoading ? (
        <div className="text-gray-400">Loading...</div>
      ) : (
        <div className="bg-gray-900 border border-gray-800 rounded-lg overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-800 text-gray-400">
                <th className="text-left px-4 py-3 font-medium">MAC</th>
                <th className="text-left px-4 py-3 font-medium">Type</th>
                <th className="text-left px-4 py-3 font-medium">Manufacturer</th>
                <th className="text-left px-4 py-3 font-medium">SSIDs</th>
                <th className="text-left px-4 py-3 font-medium">Status</th>
                <th className="text-left px-4 py-3 font-medium">Capabilities</th>
                <th className="text-left px-4 py-3 font-medium">Last Seen</th>
              </tr>
            </thead>
            <tbody>
              {devices.map((d) => (
                <tr key={d.mac} className="border-b border-gray-800/50 hover:bg-gray-800/30">
                  <td className="px-4 py-3">
                    <Link to={`/devices/${encodeURIComponent(d.mac)}`} className="text-blue-400 hover:underline font-mono text-xs">
                      {d.mac}
                    </Link>
                    {d.known_label && <div className="text-xs text-gray-400">{d.known_label}</div>}
                  </td>
                  <td className="px-4 py-3">
                    <span className={`text-xs px-2 py-0.5 rounded ${d.device_type === 'AP' ? 'bg-purple-500/20 text-purple-400' : 'bg-cyan-500/20 text-cyan-400'}`}>
                      {d.device_type}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-gray-300">{d.manufacturer || <span className="text-gray-600">-</span>}</td>
                  <td className="px-4 py-3 text-gray-300 max-w-48 truncate">{d.ssids || <span className="text-gray-600">-</span>}</td>
                  <td className="px-4 py-3">
                    <span className={`text-xs px-2 py-0.5 rounded ${statusColors[d.known_status] || statusColors.unknown}`}>
                      {d.known_status || 'unknown'}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-xs text-gray-400">
                    {[d.he_capable && 'WiFi 6', d.vht_capable && 'ac', d.ht_capable && 'n'].filter(Boolean).join(', ') || '-'}
                  </td>
                  <td className="px-4 py-3 text-gray-400 text-xs">{timeAgo(d.last_seen)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-center gap-4">
          <button
            onClick={() => setPage(page - 1)}
            disabled={page <= 1}
            className="flex items-center gap-1 text-sm text-gray-400 hover:text-white disabled:opacity-30"
          >
            <ChevronLeft className="w-4 h-4" /> Prev
          </button>
          <span className="text-sm text-gray-400">
            Page {page} of {totalPages}
          </span>
          <button
            onClick={() => setPage(page + 1)}
            disabled={page >= totalPages}
            className="flex items-center gap-1 text-sm text-gray-400 hover:text-white disabled:opacity-30"
          >
            Next <ChevronRight className="w-4 h-4" />
          </button>
        </div>
      )}
    </div>
  )
}
