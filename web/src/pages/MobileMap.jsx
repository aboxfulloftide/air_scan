import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { MapContainer, TileLayer, CircleMarker, Circle, Polyline, Polygon, Tooltip, useMapEvents, useMap } from 'react-leaflet'
import L from 'leaflet'
import api from '../api/client'
import { Search, X, Navigation } from 'lucide-react'

// ── Tile providers (same as MapView) ──
const TILE_PROVIDERS = {
  google: { label: 'Google', url: 'https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}', maxNative: 21, attr: 'Imagery &copy; Google' },
  bing: { label: 'Bing', url: 'https://ecn.t{s}.tiles.virtualearth.net/tiles/a{q}?g=1&n=z', maxNative: 20, attr: 'Imagery &copy; Microsoft', subdomains: '0123', isBing: true },
}

function BingTileLayer({ provider }) {
  const map = useMap()
  const layerRef = useRef(null)
  useEffect(() => {
    const BingLayer = L.TileLayer.extend({
      getTileUrl(coords) {
        let quadkey = ''
        for (let i = coords.z; i > 0; i--) {
          let digit = 0
          const mask = 1 << (i - 1)
          if ((coords.x & mask) !== 0) digit++
          if ((coords.y & mask) !== 0) digit += 2
          quadkey += digit
        }
        return `https://ecn.t${(coords.x + coords.y) % 4}.tiles.virtualearth.net/tiles/a${quadkey}?g=1&n=z`
      },
    })
    const layer = new BingLayer('', { maxZoom: 22, maxNativeZoom: provider.maxNative, attribution: provider.attr })
    layer.addTo(map)
    layerRef.current = layer
    return () => { map.removeLayer(layer) }
  }, [map, provider])
  return null
}

function AutoTileLayer({ zoom, provider }) {
  const p = TILE_PROVIDERS[provider] || TILE_PROVIDERS.google
  if (zoom > p.maxNative) {
    return <TileLayer key="street" attribution='&copy; OpenStreetMap'
      url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
      maxZoom={22} maxNativeZoom={19} />
  }
  if (p.isBing) return <BingTileLayer key="bing" provider={p} />
  return <TileLayer key={`sat-${provider}`} attribution={p.attr}
    url={p.url} maxZoom={22} maxNativeZoom={p.maxNative} />
}

function ZoomTracker({ onZoomChange }) {
  const map = useMap()
  useMapEvents({ zoomend: () => onZoomChange(map.getZoom()) })
  useEffect(() => { onZoomChange(map.getZoom()) }, [map, onZoomChange])
  return null
}

function FitBounds({ points }) {
  const map = useMap()
  const prevLen = useRef(0)
  useEffect(() => {
    if (!points.length) return
    // Fit on first data load, and refit when points appear after being empty
    if (prevLen.current === 0) {
      const bounds = L.latLngBounds(points)
      map.fitBounds(bounds, { padding: [30, 30], maxZoom: 18 })
    }
    prevLen.current = points.length
  }, [points, map])
  return null
}

// Signal strength color
const signalColor = (dbm) => {
  if (dbm == null) return '#6b7280'
  if (dbm >= -50) return '#22c55e'
  if (dbm >= -65) return '#3b82f6'
  if (dbm >= -75) return '#f59e0b'
  return '#ef4444'
}

const signalBorder = (dbm) => {
  if (dbm == null) return '#9ca3af'
  if (dbm >= -50) return '#86efac'
  if (dbm >= -65) return '#93c5fd'
  if (dbm >= -75) return '#fcd34d'
  return '#fca5a5'
}

// BLE uses purple palette
const bleColor = (dbm) => {
  if (dbm == null) return '#7c3aed'
  if (dbm >= -50) return '#a78bfa'
  if (dbm >= -65) return '#8b5cf6'
  if (dbm >= -75) return '#7c3aed'
  return '#6d28d9'
}

const bleBorder = (dbm) => {
  if (dbm == null) return '#c4b5fd'
  if (dbm >= -50) return '#ddd6fe'
  if (dbm >= -65) return '#c4b5fd'
  if (dbm >= -75) return '#a78bfa'
  return '#8b5cf6'
}

// Density color for route mode (device count at a position)
const densityColor = (count) => {
  if (count >= 20) return '#ef4444'
  if (count >= 10) return '#f59e0b'
  if (count >= 5)  return '#3b82f6'
  return '#22c55e'
}

// Build arc wedge polygon points (for sweep markers)
const arcPoints = (lat, lon, bearing, spreadDeg, radiusM) => {
  const toRad = (d) => d * Math.PI / 180
  const toDeg = (r) => r * 180 / Math.PI
  const R = 6371000 // earth radius meters
  const steps = 12
  const points = [[lat, lon]]
  const startAngle = bearing - spreadDeg / 2
  for (let i = 0; i <= steps; i++) {
    const angle = toRad(startAngle + (spreadDeg * i) / steps)
    const lat1 = toRad(lat)
    const lon1 = toRad(lon)
    const d = radiusM / R
    const lat2 = Math.asin(Math.sin(lat1) * Math.cos(d) + Math.cos(lat1) * Math.sin(d) * Math.cos(angle))
    const lon2 = lon1 + Math.atan2(Math.sin(angle) * Math.sin(d) * Math.cos(lat1), Math.cos(d) - Math.sin(lat1) * Math.sin(lat2))
    points.push([toDeg(lat2), toDeg(lon2)])
  }
  points.push([lat, lon])
  return points
}

// Compute bearing between two GPS points
const bearing = (lat1, lon1, lat2, lon2) => {
  const toRad = (d) => d * Math.PI / 180
  const toDeg = (r) => r * 180 / Math.PI
  const dLon = toRad(lon2 - lon1)
  const y = Math.sin(dLon) * Math.cos(toRad(lat2))
  const x = Math.cos(toRad(lat1)) * Math.sin(toRad(lat2)) - Math.sin(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.cos(dLon)
  return (toDeg(Math.atan2(y, x)) + 360) % 360
}

export default function MobileMap() {
  const [tileProvider, setTileProvider] = useState('google')
  const [zoom, setZoom] = useState(16)
  const handleZoomChange = useCallback((z) => setZoom(z), [])

  // Filters
  const [selectedSession, setSelectedSession] = useState('')
  const [minutes, setMinutes] = useState(60)
  const [searchMac, setSearchMac] = useState('')
  const [trackedMac, setTrackedMac] = useState(null)
  const [viewMode, setViewMode] = useState('points') // 'points' | 'heatmap' | 'route' | 'clusters' | 'sweep'
  const [radioFilter, setRadioFilter] = useState('all') // 'all' | 'wifi' | 'ble'
  const [selectedGroup, setSelectedGroup] = useState(null) // { macs: Set, label: string, index: number }

  // Sessions list
  const { data: sessions } = useQuery({
    queryKey: ['mobileSessions'],
    queryFn: () => api.get('/mobile/sessions').then((r) => r.data),
  })

  // Observations
  const obsParams = useMemo(() => {
    const p = {}
    if (selectedSession) p.session_id = selectedSession
    else p.minutes = minutes
    if (searchMac) p.mac = searchMac
    return p
  }, [selectedSession, minutes, searchMac])

  const { data: observations } = useQuery({
    queryKey: ['mobileObs', obsParams],
    queryFn: () => api.get('/mobile/observations', { params: obsParams }).then((r) => r.data),
    refetchInterval: selectedSession ? false : 15000,
  })

  // Heatmap data
  const { data: heatmapData } = useQuery({
    queryKey: ['mobileHeatmap', obsParams],
    queryFn: () => api.get('/mobile/heatmap', { params: obsParams }).then((r) => r.data),
    refetchInterval: selectedSession ? false : 15000,
    enabled: viewMode === 'heatmap',
  })

  // Device track
  const trackParams = useMemo(() => {
    const p = {}
    if (selectedSession) p.session_id = selectedSession
    else p.minutes = minutes
    return p
  }, [selectedSession, minutes])

  const { data: trackData } = useQuery({
    queryKey: ['mobileTrack', trackedMac, trackParams],
    queryFn: () => api.get(`/mobile/track/${encodeURIComponent(trackedMac)}`, { params: trackParams }).then((r) => r.data),
    enabled: !!trackedMac,
    refetchInterval: selectedSession ? false : 15000,
  })

  // Compute bounds from data
  const boundsPoints = useMemo(() => {
    if (viewMode === 'heatmap' && heatmapData?.length) {
      return heatmapData.map((h) => [parseFloat(h.lat), parseFloat(h.lon)])
    }
    if (observations?.length) {
      return observations
        .filter((o) => o.gps_lat && o.gps_lon)
        .map((o) => [parseFloat(o.gps_lat), parseFloat(o.gps_lon)])
    }
    return []
  }, [observations, heatmapData, viewMode])

  // Track polyline
  const trackLine = useMemo(() => {
    if (!trackData?.length) return []
    return trackData.map((t) => [parseFloat(t.gps_lat), parseFloat(t.gps_lon)])
  }, [trackData])

  const sessionList = Array.isArray(sessions) ? sessions : []
  const allObs = Array.isArray(observations) ? observations : []
  const obsList = useMemo(() => {
    if (radioFilter === 'all') return allObs
    if (radioFilter === 'ble') return allObs.filter((o) => o.device_type === 'BLE')
    return allObs.filter((o) => o.device_type !== 'BLE')
  }, [allObs, radioFilter])

  // Unique devices for device list (respects radio filter)
  const deviceSummary = useMemo(() => {
    if (!obsList.length) return []
    const map = new Map()
    for (const o of obsList) {
      const existing = map.get(o.mac)
      if (!existing || new Date(o.recorded_at) > new Date(existing.recorded_at)) {
        map.set(o.mac, o)
      }
    }
    return Array.from(map.values()).sort((a, b) => (b.signal_dbm || -100) - (a.signal_dbm || -100))
  }, [obsList])

  // ── Route mode: build colored polyline segments by scanner GPS path ──
  const routeSegments = useMemo(() => {
    if (viewMode !== 'route' || !obsList.length) return []
    // Group by recorded_at (snapshot time) to get scanner positions + device counts
    const snapshots = new Map()
    for (const o of obsList) {
      if (!o.gps_lat || !o.gps_lon) continue
      const key = o.recorded_at
      if (!snapshots.has(key)) {
        snapshots.set(key, { lat: parseFloat(o.gps_lat), lon: parseFloat(o.gps_lon), macs: new Set(), ts: o.recorded_at, bestSignal: o.signal_dbm })
      }
      const snap = snapshots.get(key)
      snap.macs.add(o.mac)
      if (o.signal_dbm != null && (snap.bestSignal == null || o.signal_dbm > snap.bestSignal)) {
        snap.bestSignal = o.signal_dbm
      }
    }
    // Sort by time and build segments
    const sorted = Array.from(snapshots.values()).sort((a, b) => a.ts.localeCompare(b.ts))
    const segments = []
    for (let i = 1; i < sorted.length; i++) {
      const prev = sorted[i - 1]
      const curr = sorted[i]
      segments.push({
        positions: [[prev.lat, prev.lon], [curr.lat, curr.lon]],
        count: curr.macs.size,
        macs: curr.macs,
        bestSignal: curr.bestSignal,
        ts: curr.ts,
      })
    }
    return segments
  }, [obsList, viewMode])

  // ── Clusters mode: group nearby observations into aggregate circles ──
  const clusters = useMemo(() => {
    if (viewMode !== 'clusters' || !obsList.length) return []
    const gpsObs = obsList.filter((o) => o.gps_lat && o.gps_lon)
    if (!gpsObs.length) return []
    // Simple grid clustering at ~50m resolution (~0.0005 degrees)
    const grid = new Map()
    const res = 0.0005
    for (const o of gpsObs) {
      const lat = parseFloat(o.gps_lat)
      const lon = parseFloat(o.gps_lon)
      const gx = Math.round(lat / res)
      const gy = Math.round(lon / res)
      const key = `${gx},${gy}`
      if (!grid.has(key)) {
        grid.set(key, { sumLat: 0, sumLon: 0, n: 0, macs: new Set(), sumSignal: 0, sigCount: 0, bleCount: 0, wifiCount: 0 })
      }
      const c = grid.get(key)
      c.sumLat += lat
      c.sumLon += lon
      c.n++
      c.macs.add(o.mac)
      if (o.signal_dbm != null) { c.sumSignal += o.signal_dbm; c.sigCount++ }
      if (o.device_type === 'BLE') c.bleCount++
      else c.wifiCount++
    }
    return Array.from(grid.values()).map((c) => ({
      lat: c.sumLat / c.n,
      lon: c.sumLon / c.n,
      obsCount: c.n,
      deviceCount: c.macs.size,
      macs: c.macs,
      avgSignal: c.sigCount ? c.sumSignal / c.sigCount : null,
      bleCount: c.bleCount,
      wifiCount: c.wifiCount,
    }))
  }, [obsList, viewMode])

  // ── Sweep mode: fan markers showing direction of travel + detection count ──
  const sweepMarkers = useMemo(() => {
    if (viewMode !== 'sweep' || !obsList.length) return []
    // Group by snapshot time
    const snapshots = new Map()
    for (const o of obsList) {
      if (!o.gps_lat || !o.gps_lon) continue
      const key = o.recorded_at
      if (!snapshots.has(key)) {
        snapshots.set(key, { lat: parseFloat(o.gps_lat), lon: parseFloat(o.gps_lon), macs: new Set(), ts: o.recorded_at, avgSignal: 0, sigCount: 0 })
      }
      const snap = snapshots.get(key)
      snap.macs.add(o.mac)
      if (o.signal_dbm != null) { snap.avgSignal += o.signal_dbm; snap.sigCount++ }
    }
    const sorted = Array.from(snapshots.values()).sort((a, b) => a.ts.localeCompare(b.ts))
    // Compute bearing from previous point to current
    const markers = []
    for (let i = 0; i < sorted.length; i++) {
      const curr = sorted[i]
      const prev = i > 0 ? sorted[i - 1] : null
      const next = i < sorted.length - 1 ? sorted[i + 1] : null
      // Use forward bearing, fallback to backward
      let dir = 0
      if (next) dir = bearing(curr.lat, curr.lon, next.lat, next.lon)
      else if (prev) dir = bearing(prev.lat, prev.lon, curr.lat, curr.lon)
      const count = curr.macs.size
      const avgSig = curr.sigCount ? curr.avgSignal / curr.sigCount : -80
      // Sweep radius proportional to device count (15m base + 3m per device, max 60m)
      const radiusM = Math.min(15 + count * 3, 60)
      markers.push({
        lat: curr.lat, lon: curr.lon,
        bearing: dir, count, macs: curr.macs, avgSignal: avgSig,
        radiusM, ts: curr.ts,
      })
    }
    return markers
  }, [obsList, viewMode])

  // Sidebar devices: filter to selected group if active
  const sidebarDevices = useMemo(() => {
    if (!selectedGroup) return deviceSummary
    return deviceSummary.filter((d) => selectedGroup.macs.has(d.mac))
  }, [deviceSummary, selectedGroup])

  // Default center: 2080 Pinetree Dr, Trenton MI
  const defaultCenter = boundsPoints.length > 0 ? boundsPoints[0] : [42.1394, -83.1783]

  return (
    <div className="p-6 space-y-3 h-full flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center gap-4">
          <h2 className="text-xl font-bold text-white flex items-center gap-2">
            <Navigation className="w-5 h-5 text-emerald-400" />
            Mobile Scanner
          </h2>
          <div className="flex bg-gray-800 rounded-lg p-0.5 text-xs">
            {Object.entries(TILE_PROVIDERS).map(([key, p]) => (
              <button key={key} onClick={() => setTileProvider(key)}
                className={`px-2.5 py-1 rounded-md transition-colors ${tileProvider === key ? 'bg-gray-600 text-white' : 'text-gray-400 hover:text-gray-200'}`}>
                {p.label}
              </button>
            ))}
          </div>
        </div>

        {/* Radio type filter */}
        <div className="flex bg-gray-800 rounded-lg p-0.5 text-xs">
          {[['all', 'All'], ['wifi', 'WiFi'], ['ble', 'BLE']].map(([key, label]) => (
            <button key={key} onClick={() => setRadioFilter(key)}
              className={`px-2.5 py-1 rounded-md transition-colors ${radioFilter === key ? 'bg-gray-600 text-white' : 'text-gray-400 hover:text-gray-200'}`}>
              {label}
            </button>
          ))}
        </div>

        {/* View mode toggle */}
        <div className="flex bg-gray-800 rounded-lg p-0.5 text-xs">
          {[['points', 'Points'], ['route', 'Route'], ['clusters', 'Clusters'], ['sweep', 'Sweep'], ['heatmap', 'Heatmap']].map(([key, label]) => (
            <button key={key} onClick={() => { setViewMode(key); setSelectedGroup(null) }}
              className={`px-2.5 py-1 rounded-md transition-colors ${viewMode === key ? 'bg-gray-600 text-white' : 'text-gray-400 hover:text-gray-200'}`}>
              {label}
            </button>
          ))}
        </div>
      </div>

      {/* Filters bar */}
      <div className="flex items-center gap-3 flex-wrap">
        <select value={selectedSession} onChange={(e) => setSelectedSession(e.target.value)}
          className="bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-white">
          <option value="">Live ({minutes}m window)</option>
          {sessionList.map((s) => (
            <option key={s.session_id} value={s.session_id}>
              {s.scanner_host} - {new Date(s.started_at).toLocaleDateString()} {new Date(s.started_at).toLocaleTimeString()} ({s.device_count} devices)
            </option>
          ))}
        </select>

        {!selectedSession && (
          <div className="flex items-center gap-1">
            {[15, 30, 60, 120, 480].map((m) => (
              <button key={m} onClick={() => setMinutes(m)}
                className={`px-2 py-1 rounded text-xs transition-colors ${minutes === m ? 'bg-blue-600 text-white' : 'bg-gray-800 text-gray-400 hover:text-white'}`}>
                {m < 60 ? `${m}m` : `${m / 60}h`}
              </button>
            ))}
          </div>
        )}

        <div className="text-xs text-gray-500">
          {obsList.length} observations | {deviceSummary.length} devices
          {radioFilter === 'all' && allObs.some((o) => o.device_type === 'BLE') && (
            <span className="ml-1 text-purple-400">
              ({allObs.filter((o) => o.device_type === 'BLE').length} BLE)
            </span>
          )}
        </div>

        {trackedMac && (
          <div className="flex items-center gap-1 bg-emerald-900/40 border border-emerald-700 rounded px-2 py-1 text-xs text-emerald-300">
            Tracking: {trackedMac}
            <button onClick={() => setTrackedMac(null)} className="ml-1 hover:text-white"><X className="w-3 h-3" /></button>
          </div>
        )}
      </div>

      {/* Main content: map + sidebar */}
      <div className="flex-1 flex gap-3 min-h-0">
        {/* Map */}
        <div className="flex-1 rounded-lg overflow-hidden border border-gray-800">
          <MapContainer center={defaultCenter} zoom={16} className="h-full w-full"
            style={{ background: '#111827' }} zoomControl={false}>
            <ZoomTracker onZoomChange={handleZoomChange} />
            <AutoTileLayer zoom={zoom} provider={tileProvider} />
            <FitBounds points={boundsPoints} />

            {/* Track polyline */}
            {trackLine.length > 1 && (
              <Polyline positions={trackLine} pathOptions={{ color: '#10b981', weight: 3, opacity: 0.8, dashArray: '6 4' }} />
            )}

            {/* Observation points */}
            {viewMode === 'points' && obsList.map((o, i) => {
              const isBle = o.device_type === 'BLE'
              const fill = trackedMac === o.mac ? '#10b981' : isBle ? bleColor(o.signal_dbm) : signalColor(o.signal_dbm)
              const border = trackedMac === o.mac ? '#6ee7b7' : isBle ? bleBorder(o.signal_dbm) : signalBorder(o.signal_dbm)
              return o.gps_lat && o.gps_lon && (
                <CircleMarker key={i}
                  center={[parseFloat(o.gps_lat), parseFloat(o.gps_lon)]}
                  radius={trackedMac === o.mac ? 7 : isBle ? 4 : 5}
                  pathOptions={{
                    fillColor: fill,
                    color: border,
                    fillOpacity: 0.7, weight: 1.5,
                  }}>
                  <Tooltip direction="top" offset={[0, -6]}>
                    <div className="text-xs space-y-0.5">
                      <p className="font-bold">{o.known_label || o.mac}
                        {isBle && <span style={{ color: '#a78bfa', marginLeft: 4 }}>BLE</span>}
                      </p>
                      {o.manufacturer && <p>{o.manufacturer}</p>}
                      <p>{o.signal_dbm} dBm{o.channel ? ` | ch ${o.channel}` : ''}</p>
                      <p>{o.device_type}{o.ssids ? ` | ${o.ssids}` : ''}</p>
                      <p className="text-gray-400">{new Date(o.recorded_at).toLocaleTimeString()}</p>
                    </div>
                  </Tooltip>
                </CircleMarker>
              )
            })}

            {/* Heatmap circles */}
            {viewMode === 'heatmap' && heatmapData?.map((h, i) => {
              const isSelected = selectedGroup?.index === i && selectedGroup?.mode === 'heatmap'
              const count = h.device_count || 1
              const maxR = 20
              const r = Math.min(6 + count * 2, maxR)
              return (
                <CircleMarker key={i}
                  center={[parseFloat(h.lat), parseFloat(h.lon)]}
                  radius={isSelected ? r + 4 : r}
                  pathOptions={{
                    fillColor: isSelected ? '#10b981' : (count >= 10 ? '#ef4444' : count >= 5 ? '#f59e0b' : '#3b82f6'),
                    color: isSelected ? '#6ee7b7' : 'transparent',
                    fillOpacity: isSelected ? 0.9 : (selectedGroup?.mode === 'heatmap' ? 0.2 : Math.min(0.3 + count * 0.05, 0.8)),
                    weight: isSelected ? 2.5 : 0,
                  }}
                  eventHandlers={{
                    click: () => {
                      // Find MACs in this grid cell by matching lat/lon rounded to 4 decimals
                      const cellLat = parseFloat(h.lat)
                      const cellLon = parseFloat(h.lon)
                      const macs = new Set()
                      for (const o of obsList) {
                        if (!o.gps_lat || !o.gps_lon) continue
                        if (Math.round(parseFloat(o.gps_lat) * 10000) === Math.round(cellLat * 10000) &&
                            Math.round(parseFloat(o.gps_lon) * 10000) === Math.round(cellLon * 10000)) {
                          macs.add(o.mac)
                        }
                      }
                      setSelectedGroup(
                        isSelected ? null : { macs, label: `Heatmap cell (${count} devices)`, index: i, mode: 'heatmap' }
                      )
                    },
                  }}>
                  <Tooltip direction="top">
                    <div className="text-xs">
                      <p>{h.device_count} devices | {h.obs_count} obs — click to inspect</p>
                      <p>Avg signal: {parseFloat(h.avg_signal).toFixed(0)} dBm</p>
                    </div>
                  </Tooltip>
                </CircleMarker>
              )
            })}

            {/* Route: colored polyline segments by device density */}
            {viewMode === 'route' && routeSegments.map((seg, i) => {
              const isSelected = selectedGroup?.index === i && selectedGroup?.mode === 'route'
              return (
                <Polyline key={i}
                  positions={seg.positions}
                  pathOptions={{
                    color: isSelected ? '#ffffff' : densityColor(seg.count),
                    weight: isSelected ? Math.min(3 + seg.count, 10) + 3 : Math.min(3 + seg.count, 10),
                    opacity: isSelected ? 1 : (selectedGroup?.mode === 'route' ? 0.4 : 0.85),
                    lineCap: 'round', lineJoin: 'round',
                  }}
                  eventHandlers={{
                    click: () => setSelectedGroup(
                      isSelected ? null : { macs: seg.macs, label: `Route @ ${new Date(seg.ts).toLocaleTimeString()}`, index: i, mode: 'route' }
                    ),
                  }}>
                  <Tooltip direction="top">
                    <div className="text-xs">
                      <p>{seg.count} devices — click to inspect</p>
                      <p>Best signal: {seg.bestSignal} dBm</p>
                      <p className="text-gray-400">{new Date(seg.ts).toLocaleTimeString()}</p>
                    </div>
                  </Tooltip>
                </Polyline>
              )
            })}

            {/* Clusters: aggregated circles */}
            {viewMode === 'clusters' && clusters.map((c, i) => {
              const isSelected = selectedGroup?.index === i && selectedGroup?.mode === 'clusters'
              const r = Math.min(8 + c.deviceCount * 2, 30)
              const bleRatio = c.obsCount > 0 ? c.bleCount / c.obsCount : 0
              const baseFill = bleRatio > 0.5 ? '#8b5cf6' : densityColor(c.deviceCount)
              return (
                <CircleMarker key={i}
                  center={[c.lat, c.lon]}
                  radius={isSelected ? r + 4 : r}
                  pathOptions={{
                    fillColor: isSelected ? '#10b981' : baseFill,
                    color: isSelected ? '#6ee7b7' : (bleRatio > 0.5 ? '#c4b5fd' : '#ffffff'),
                    fillOpacity: isSelected ? 0.9 : (selectedGroup?.mode === 'clusters' ? 0.25 : Math.min(0.4 + c.deviceCount * 0.04, 0.85)),
                    weight: isSelected ? 3 : 1.5,
                  }}
                  eventHandlers={{
                    click: () => setSelectedGroup(
                      isSelected ? null : { macs: c.macs, label: `Cluster (${c.deviceCount} devices)`, index: i, mode: 'clusters' }
                    ),
                  }}>
                  <Tooltip direction="top">
                    <div className="text-xs space-y-0.5">
                      <p className="font-bold">{c.deviceCount} devices — click to inspect</p>
                      <p>{c.obsCount} observations</p>
                      {c.wifiCount > 0 && <p>WiFi: {c.wifiCount}</p>}
                      {c.bleCount > 0 && <p style={{ color: '#a78bfa' }}>BLE: {c.bleCount}</p>}
                      {c.avgSignal != null && <p>Avg signal: {c.avgSignal.toFixed(0)} dBm</p>}
                    </div>
                  </Tooltip>
                </CircleMarker>
              )
            })}

            {/* Sweep: arc/fan markers showing direction + density */}
            {viewMode === 'sweep' && sweepMarkers.map((m, i) => {
              const isSelected = selectedGroup?.index === i && selectedGroup?.mode === 'sweep'
              const pts = arcPoints(m.lat, m.lon, m.bearing, 120, m.radiusM)
              return (
                <Polygon key={i}
                  positions={pts}
                  pathOptions={{
                    fillColor: isSelected ? '#10b981' : densityColor(m.count),
                    color: isSelected ? '#6ee7b7' : densityColor(m.count),
                    fillOpacity: isSelected ? 0.6 : (selectedGroup?.mode === 'sweep' ? 0.15 : 0.35),
                    weight: isSelected ? 2.5 : 1,
                  }}
                  eventHandlers={{
                    click: () => setSelectedGroup(
                      isSelected ? null : { macs: m.macs, label: `Sweep @ ${new Date(m.ts).toLocaleTimeString()}`, index: i, mode: 'sweep' }
                    ),
                  }}>
                  <Tooltip direction="top">
                    <div className="text-xs space-y-0.5">
                      <p className="font-bold">{m.count} devices — click to inspect</p>
                      <p>Avg signal: {m.avgSignal.toFixed(0)} dBm</p>
                      <p>Heading: {m.bearing.toFixed(0)}&deg;</p>
                      <p className="text-gray-400">{new Date(m.ts).toLocaleTimeString()}</p>
                    </div>
                  </Tooltip>
                </Polygon>
              )
            })}
          </MapContainer>
        </div>

        {/* Device sidebar */}
        <div className="w-72 bg-gray-900 border border-gray-800 rounded-lg flex flex-col overflow-hidden">
          {/* Group selection header */}
          {selectedGroup && (
            <div className="px-3 py-2 bg-emerald-900/30 border-b border-emerald-700 flex items-center justify-between">
              <div>
                <p className="text-xs text-emerald-400 font-medium">{selectedGroup.label}</p>
                <p className="text-xs text-emerald-300/60">{selectedGroup.macs.size} devices</p>
              </div>
              <button onClick={() => setSelectedGroup(null)} className="text-emerald-400 hover:text-white">
                <X className="w-4 h-4" />
              </button>
            </div>
          )}
          <div className="p-3 border-b border-gray-800">
            <div className="relative">
              <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-500" />
              <input
                type="text"
                placeholder="Filter by MAC..."
                value={searchMac}
                onChange={(e) => setSearchMac(e.target.value)}
                className="w-full bg-gray-800 border border-gray-700 rounded pl-8 pr-2 py-1.5 text-sm text-white placeholder-gray-500"
              />
              {searchMac && (
                <button onClick={() => setSearchMac('')} className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-500 hover:text-white">
                  <X className="w-3.5 h-3.5" />
                </button>
              )}
            </div>
          </div>
          <div className="flex-1 overflow-y-auto">
            {sidebarDevices.map((d) => (
              <button key={d.mac}
                onClick={() => setTrackedMac(trackedMac === d.mac ? null : d.mac)}
                className={`w-full text-left px-3 py-2 border-b border-gray-800/50 hover:bg-gray-800/50 transition-colors ${
                  trackedMac === d.mac ? 'bg-emerald-900/20 border-l-2 border-l-emerald-400' : ''
                }`}>
                <div className="flex items-center justify-between">
                  <span className="text-sm text-white truncate">
                    {d.known_label || d.mac}
                    {d.device_type === 'BLE' && <span className="ml-1 text-purple-400 text-xs">BLE</span>}
                  </span>
                  <span className="text-xs ml-2 shrink-0" style={{ color: d.device_type === 'BLE' ? bleColor(d.signal_dbm) : signalColor(d.signal_dbm) }}>
                    {d.signal_dbm} dBm
                  </span>
                </div>
                <div className="text-xs text-gray-500 truncate">
                  {d.device_type}{d.manufacturer ? ` | ${d.manufacturer}` : ''}{d.ssids ? ` | ${d.ssids}` : ''}
                </div>
              </button>
            ))}
            {sidebarDevices.length === 0 && (
              <p className="text-sm text-gray-500 p-3">{selectedGroup ? 'No matching devices' : 'No observations'}</p>
            )}
          </div>
        </div>
      </div>

      {/* Legend */}
      <div className="flex items-center gap-4 text-xs text-gray-500 flex-wrap">
        {viewMode === 'points' && <>
          <span>WiFi:</span>
          <div className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-full bg-green-500" /> Strong (&gt;-50)</div>
          <div className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-full bg-blue-500" /> Good (-50 to -65)</div>
          <div className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-full bg-yellow-500" /> Fair (-65 to -75)</div>
          <div className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-full bg-red-500" /> Weak (&lt;-75)</div>
          <span className="ml-2">BLE:</span>
          <div className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-full" style={{ background: '#a78bfa' }} /> Purple</div>
        </>}
        {(viewMode === 'route' || viewMode === 'clusters' || viewMode === 'sweep') && <>
          <span>Density:</span>
          <div className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-full bg-green-500" /> &lt;5</div>
          <div className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-full bg-blue-500" /> 5-9</div>
          <div className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-full bg-yellow-500" /> 10-19</div>
          <div className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-full bg-red-500" /> 20+</div>
          {viewMode === 'clusters' && <>
            <span className="ml-2">BLE majority:</span>
            <div className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-full" style={{ background: '#8b5cf6' }} /> Purple</div>
          </>}
          {viewMode === 'route' && <span className="ml-2 text-gray-600">Line width = device count</span>}
          {viewMode === 'sweep' && <span className="ml-2 text-gray-600">Fan size = device count | direction = travel heading</span>}
        </>}
        {viewMode === 'heatmap' && <>
          <span>Density:</span>
          <div className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-full bg-blue-500" /> Low</div>
          <div className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-full bg-yellow-500" /> Medium</div>
          <div className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-full bg-red-500" /> High</div>
        </>}
      </div>
    </div>
  )
}
