import { useState, useEffect, useRef, useCallback } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { MapContainer, TileLayer, Marker, Popup, CircleMarker, Tooltip, useMapEvents, useMap } from 'react-leaflet'
import L from 'leaflet'
import api from '../api/client'
import { Search, Crosshair, Trash2, X, BarChart3, Radio } from 'lucide-react'
import { SavedWalls } from '../components/MapDrawing'

// Numbered calibration point icon
const pointIcon = (num, hasReadings) =>
  L.divIcon({
    className: '',
    html: `<div class="flex items-center justify-center w-7 h-7 rounded-full border-2 ${
      hasReadings ? 'bg-emerald-500/30 border-emerald-400' : 'bg-gray-500/30 border-gray-400'
    } text-xs font-bold text-white">${num}</div>`,
    iconSize: [28, 28],
    iconAnchor: [14, 14],
  })

// Pending click icon (crosshair)
const pendingIcon = L.divIcon({
  className: '',
  html: `<div class="flex items-center justify-center w-10 h-10 rounded-full border-2 border-dashed bg-yellow-500/15 border-yellow-400 animate-pulse"><svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="text-yellow-400"><circle cx="12" cy="12" r="10"/><line x1="12" x2="12" y1="2" y2="6"/><line x1="12" x2="12" y1="18" y2="22"/><line x1="2" x2="6" y1="12" y2="12"/><line x1="18" x2="22" y1="12" y2="12"/></svg></div>`,
  iconSize: [40, 40],
  iconAnchor: [20, 20],
})

const scannerIcon = L.divIcon({
  className: '',
  html: `<div class="flex items-center justify-center w-8 h-8 rounded-full border-2 bg-blue-500/30 border-blue-400"><svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="text-blue-400"><path d="M4.9 19.1C1 15.2 1 8.8 4.9 4.9"/><path d="M7.8 16.2c-2.3-2.3-2.3-6.1 0-8.5"/><circle cx="12" cy="12" r="2"/><path d="M16.2 7.8c2.3 2.3 2.3 6.1 0 8.5"/><path d="M19.1 4.9C23 8.8 23 15.1 19.1 19"/></svg></div>`,
  iconSize: [32, 32],
  iconAnchor: [16, 16],
})

function MapRecenter({ scanners, fallbackLat, fallbackLon }) {
  const map = useMap()
  const done = useRef(false)
  useEffect(() => {
    if (done.current) return
    if (scanners && scanners.length >= 2) {
      const bounds = scanners.map(s => [Number(s.x_pos), Number(s.y_pos)])
      map.fitBounds(bounds, { padding: [60, 60], maxZoom: 21 })
      done.current = true
    } else if (scanners && scanners.length === 1) {
      map.setView([Number(scanners[0].x_pos), Number(scanners[0].y_pos)], 20)
      done.current = true
    } else if (fallbackLat && fallbackLon) {
      map.setView([fallbackLat, fallbackLon], 20)
      done.current = true
    }
  }, [scanners, fallbackLat, fallbackLon, map])
  return null
}

function ClickHandler({ active, onClick }) {
  useMapEvents({
    click(e) {
      if (active) onClick(e.latlng.lat, e.latlng.lng)
    },
  })
  return null
}

export default function Calibrate() {
  const queryClient = useQueryClient()
  const [mac, setMac] = useState('')
  const [macLocked, setMacLocked] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')
  const [pendingPoint, setPendingPoint] = useState(null) // {lat, lon} awaiting confirm
  const [label, setLabel] = useState('')
  const [window, setWindow] = useState(30)
  const [capturing, setCapturing] = useState(false)
  const [lastResult, setLastResult] = useState(null)

  // Data queries
  const { data: mapConfig } = useQuery({
    queryKey: ['mapConfig'],
    queryFn: () => api.get('/maps/config').then(r => r.data),
  })

  const { data: scanners } = useQuery({
    queryKey: ['scanners'],
    queryFn: () => api.get('/scanners/').then(r => r.data),
    refetchInterval: 30000,
  })

  const { data: points, isLoading: pointsLoading } = useQuery({
    queryKey: ['calibrationPoints'],
    queryFn: () => api.get('/calibration/points').then(r => r.data),
    refetchInterval: 10000,
  })

  const { data: summary } = useQuery({
    queryKey: ['calibrationSummary'],
    queryFn: () => api.get('/calibration/summary').then(r => r.data),
    refetchInterval: 15000,
  })

  const { data: walls } = useQuery({
    queryKey: ['walls'],
    queryFn: () => api.get('/maps/walls').then(r => r.data),
  })

  const { data: searchResults } = useQuery({
    queryKey: ['deviceSearch', searchQuery],
    queryFn: () => api.get('/maps/devices/search', { params: { q: searchQuery } }).then(r => r.data),
    enabled: searchQuery.length >= 2,
  })

  // Mutations
  const captureMut = useMutation({
    mutationFn: (body) => api.post('/calibration/capture', body).then(r => r.data),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['calibrationPoints'] })
      queryClient.invalidateQueries({ queryKey: ['calibrationSummary'] })
      setLastResult(data)
      setPendingPoint(null)
      setLabel('')
    },
    onSettled: () => setCapturing(false),
  })

  const deleteMut = useMutation({
    mutationFn: (id) => api.delete(`/calibration/points/${id}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['calibrationPoints'] })
      queryClient.invalidateQueries({ queryKey: ['calibrationSummary'] })
    },
  })

  const deleteAllMut = useMutation({
    mutationFn: () => api.delete('/calibration/points'),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['calibrationPoints'] })
      queryClient.invalidateQueries({ queryKey: ['calibrationSummary'] })
    },
  })

  const handleMapClick = useCallback((lat, lon) => {
    if (!macLocked) return
    setPendingPoint({ lat, lon })
    setLastResult(null)
  }, [macLocked])

  const handleCapture = () => {
    if (!pendingPoint || !mac) return
    setCapturing(true)
    captureMut.mutate({
      mac,
      lat: pendingPoint.lat,
      lon: pendingPoint.lon,
      label,
      window_seconds: window,
    })
  }

  const handleSelectDevice = (device) => {
    setMac(device.mac)
    setMacLocked(true)
    setSearchQuery('')
  }

  const centerLat = mapConfig?.gps_anchor_lat
  const centerLon = mapConfig?.gps_anchor_lon
  const activeScanners = (scanners || []).filter(s => s.x_pos != null && s.y_pos != null)
  const filteredPoints = macLocked ? (points || []).filter(p => p.mac === mac) : (points || [])

  return (
    <div className="flex h-full">
      {/* Sidebar */}
      <div className="w-80 bg-gray-900 border-r border-gray-800 flex flex-col overflow-hidden">
        <div className="px-4 py-4 border-b border-gray-800">
          <h2 className="text-lg font-semibold text-white flex items-center gap-2">
            <Crosshair className="w-5 h-5 text-emerald-400" />
            Calibration
          </h2>
          <p className="text-xs text-gray-400 mt-1">
            Walk a device to known spots, click the map to capture RSSI readings.
          </p>
        </div>

        {/* Device picker */}
        <div className="px-4 py-3 border-b border-gray-800 space-y-2">
          <label className="text-xs text-gray-400 font-medium">Device MAC</label>
          {macLocked ? (
            <div className="flex items-center justify-between bg-gray-800 rounded-lg px-3 py-2">
              <span className="text-sm text-white font-mono">{mac}</span>
              <button onClick={() => { setMacLocked(false); setMac(''); setPendingPoint(null) }}
                className="text-gray-400 hover:text-white"><X className="w-4 h-4" /></button>
            </div>
          ) : (
            <div className="space-y-2">
              <div className="relative">
                <Search className="absolute left-3 top-2.5 w-4 h-4 text-gray-500" />
                <input type="text" value={searchQuery} onChange={e => setSearchQuery(e.target.value)}
                  placeholder="Search by name, MAC, owner..."
                  className="w-full bg-gray-800 border border-gray-700 rounded-lg pl-9 pr-3 py-2 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-emerald-500" />
              </div>
              {searchResults?.length > 0 && (
                <div className="max-h-40 overflow-y-auto space-y-1">
                  {searchResults.map(d => (
                    <button key={d.mac} onClick={() => handleSelectDevice(d)}
                      className="w-full text-left bg-gray-800 hover:bg-gray-700 rounded px-3 py-2">
                      <span className="text-sm text-white">{d.known_label || d.ssids || d.mac}</span>
                      {(d.known_label || d.ssids) && <span className="text-xs text-gray-500 ml-2 font-mono">{d.mac}</span>}
                      {(d.owner || d.manufacturer) && <div className="text-xs text-gray-500">{d.owner || d.manufacturer}</div>}
                    </button>
                  ))}
                </div>
              )}
              <div className="text-xs text-gray-500">Or paste directly:</div>
              <div className="flex gap-2">
                <input type="text" value={mac} onChange={e => setMac(e.target.value.toLowerCase())}
                  placeholder="aa:bb:cc:dd:ee:ff"
                  className="flex-1 bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white font-mono placeholder-gray-500 focus:outline-none focus:border-emerald-500" />
                <button onClick={() => { if (mac.length >= 17) setMacLocked(true) }}
                  disabled={mac.length < 17}
                  className="px-3 py-2 bg-emerald-600 hover:bg-emerald-500 disabled:bg-gray-700 disabled:text-gray-500 text-white text-sm rounded-lg">
                  Lock
                </button>
              </div>
            </div>
          )}
        </div>

        {/* Capture controls */}
        {macLocked && (
          <div className="px-4 py-3 border-b border-gray-800 space-y-2">
            {pendingPoint ? (
              <>
                <div className="text-xs text-gray-400">
                  Point at ({pendingPoint.lat.toFixed(6)}, {pendingPoint.lon.toFixed(6)})
                </div>
                <input type="text" value={label} onChange={e => setLabel(e.target.value)}
                  placeholder="Label (optional, e.g. kitchen corner)"
                  className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-emerald-500" />
                <div className="flex items-center gap-2">
                  <label className="text-xs text-gray-400 whitespace-nowrap">Window:</label>
                  <select value={window} onChange={e => setWindow(Number(e.target.value))}
                    className="flex-1 bg-gray-800 border border-gray-700 rounded text-sm text-white px-2 py-1">
                    <option value={15}>15s</option>
                    <option value={30}>30s</option>
                    <option value={60}>60s</option>
                    <option value={120}>120s</option>
                  </select>
                </div>
                <div className="flex gap-2">
                  <button onClick={handleCapture} disabled={capturing}
                    className="flex-1 px-3 py-2 bg-emerald-600 hover:bg-emerald-500 disabled:bg-gray-700 text-white text-sm rounded-lg font-medium">
                    {capturing ? 'Capturing...' : 'Capture RSSI'}
                  </button>
                  <button onClick={() => setPendingPoint(null)}
                    className="px-3 py-2 bg-gray-700 hover:bg-gray-600 text-white text-sm rounded-lg">
                    Cancel
                  </button>
                </div>
                {captureMut.isError && (
                  <div className="text-xs text-red-400">
                    {captureMut.error?.response?.data?.[0]?.error || captureMut.error?.response?.data?.error || 'Capture failed'}
                  </div>
                )}
              </>
            ) : (
              <div className="text-sm text-gray-400 py-2">
                Click on the map where the device is located.
              </div>
            )}
            {lastResult && lastResult.ok && (
              <div className="bg-emerald-900/30 border border-emerald-800 rounded-lg px-3 py-2 text-xs">
                <div className="text-emerald-400 font-medium">Point captured</div>
                <div className="text-gray-300">{lastResult.scanner_count} scanner(s), {lastResult.readings?.length || 0} readings</div>
              </div>
            )}
          </div>
        )}

        {/* Summary / fit stats */}
        {summary && summary.point_count > 0 && (
          <div className="px-4 py-3 border-b border-gray-800 space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-xs text-gray-400 font-medium flex items-center gap-1">
                <BarChart3 className="w-3.5 h-3.5" /> Path-Loss Fit
              </span>
              <span className="text-xs text-gray-500">{summary.point_count} points</span>
            </div>
            {summary.fit ? (
              <div className="bg-gray-800 rounded-lg px-3 py-2 space-y-1 text-xs">
                <div className="flex justify-between">
                  <span className="text-gray-400">TX Power (1m)</span>
                  <span className="text-white font-mono">{summary.fit.tx_power} dBm</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-gray-400">Path-loss n</span>
                  <span className="text-white font-mono">{summary.fit.path_loss_n}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-gray-400">R²</span>
                  <span className={`font-mono ${summary.fit.r_squared >= 0.7 ? 'text-emerald-400' : summary.fit.r_squared >= 0.4 ? 'text-yellow-400' : 'text-red-400'}`}>
                    {summary.fit.r_squared}
                  </span>
                </div>
                {!summary.fit.reasonable && (
                  <div className="text-yellow-400 mt-1">Fit outside reasonable range — need more points</div>
                )}
              </div>
            ) : (
              <div className="text-xs text-gray-500">Need at least 4 readings for a fit</div>
            )}
            {Object.keys(summary.scanner_coverage).length > 0 && (
              <div className="space-y-1">
                <span className="text-xs text-gray-500">Scanner coverage:</span>
                {Object.entries(summary.scanner_coverage).map(([host, count]) => (
                  <div key={host} className="flex justify-between text-xs">
                    <span className="text-gray-400 font-mono truncate">{host}</span>
                    <span className="text-gray-300">{count} pts</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Point list */}
        <div className="flex-1 overflow-y-auto px-4 py-3 space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-xs text-gray-400 font-medium">Calibration Points</span>
            {(points || []).length > 0 && (
              <button onClick={() => { if (confirm('Delete all calibration points?')) deleteAllMut.mutate() }}
                className="text-xs text-red-400 hover:text-red-300">Clear all</button>
            )}
          </div>
          {pointsLoading ? (
            <div className="text-xs text-gray-500">Loading...</div>
          ) : filteredPoints.length === 0 ? (
            <div className="text-xs text-gray-500">No calibration points yet</div>
          ) : (
            filteredPoints.map((pt, idx) => (
              <div key={pt.id} className="bg-gray-800 rounded-lg px-3 py-2 space-y-1">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className="w-5 h-5 rounded-full bg-emerald-500/30 border border-emerald-400 flex items-center justify-center text-xs text-white font-bold">
                      {filteredPoints.length - idx}
                    </span>
                    <span className="text-sm text-white">{pt.label || `Point ${filteredPoints.length - idx}`}</span>
                  </div>
                  <button onClick={() => deleteMut.mutate(pt.id)} className="text-gray-500 hover:text-red-400">
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </div>
                <div className="text-xs text-gray-500 font-mono">
                  ({Number(pt.lat).toFixed(6)}, {Number(pt.lon).toFixed(6)})
                </div>
                {pt.readings && pt.readings.length > 0 && (
                  <div className="space-y-0.5 mt-1">
                    {pt.readings.map(r => (
                      <div key={r.scanner_host} className="flex items-center justify-between text-xs">
                        <span className="text-gray-400 flex items-center gap-1">
                          <Radio className="w-3 h-3 text-blue-400" />
                          {r.scanner_host}
                        </span>
                        <span className={`font-mono ${r.avg_rssi > -50 ? 'text-emerald-400' : r.avg_rssi > -70 ? 'text-yellow-400' : 'text-red-400'}`}>
                          {r.avg_rssi.toFixed(1)} dBm
                        </span>
                        <span className="text-gray-600">({r.sample_count})</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ))
          )}
        </div>
      </div>

      {/* Map */}
      <div className="flex-1 relative">
        <MapContainer center={[centerLat || 0, centerLon || 0]} zoom={20}
          className="h-full w-full" style={{ background: '#1a1a2e' }}
          zoomControl={false}>
          <TileLayer
            attribution='Imagery &copy; Google'
            url="https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}"
            maxZoom={22} maxNativeZoom={21} />
          <MapRecenter scanners={activeScanners} fallbackLat={centerLat} fallbackLon={centerLon} />
          <ClickHandler active={macLocked} onClick={handleMapClick} />

          {/* Walls */}
          {walls && <SavedWalls walls={walls} selectedId={null} onSelect={() => {}} />}

          {/* Scanners */}
          {activeScanners.map(s => (
            <Marker key={s.hostname} position={[Number(s.x_pos), Number(s.y_pos)]} icon={scannerIcon}>
              <Tooltip permanent direction="top" offset={[0, -16]}
                className="!bg-gray-900 !border-gray-700 !text-blue-400 !text-xs !px-2 !py-0.5 !rounded !shadow-lg">
                {s.label || s.hostname}
              </Tooltip>
            </Marker>
          ))}

          {/* Existing calibration points */}
          {filteredPoints.map((pt, idx) => (
            <Marker key={pt.id} position={[Number(pt.lat), Number(pt.lon)]}
              icon={pointIcon(filteredPoints.length - idx, pt.readings?.length > 0)}>
              <Popup className="!bg-gray-900 !text-white">
                <div className="text-sm">
                  <div className="font-medium">{pt.label || `Point ${filteredPoints.length - idx}`}</div>
                  <div className="text-xs text-gray-400 font-mono mt-1">{pt.mac}</div>
                  {pt.readings?.map(r => (
                    <div key={r.scanner_host} className="text-xs mt-1">
                      {r.scanner_host}: <span className="font-mono">{r.avg_rssi.toFixed(1)} dBm</span> ({r.sample_count} samples)
                    </div>
                  ))}
                </div>
              </Popup>
            </Marker>
          ))}

          {/* Pending point */}
          {pendingPoint && (
            <Marker position={[pendingPoint.lat, pendingPoint.lon]} icon={pendingIcon}>
              <Tooltip permanent direction="top" offset={[0, -20]}
                className="!bg-yellow-900/80 !border-yellow-700 !text-yellow-200 !text-xs !px-2 !py-0.5 !rounded">
                Click "Capture RSSI"
              </Tooltip>
            </Marker>
          )}
        </MapContainer>

        {/* Instructions overlay */}
        {!macLocked && (
          <div className="absolute top-4 left-1/2 -translate-x-1/2 bg-gray-900/90 border border-gray-700 rounded-lg px-4 py-2 text-sm text-gray-300 pointer-events-none">
            Select a device to start calibration
          </div>
        )}
        {macLocked && !pendingPoint && (
          <div className="absolute top-4 left-1/2 -translate-x-1/2 bg-gray-900/90 border border-emerald-800 rounded-lg px-4 py-2 text-sm text-emerald-300 pointer-events-none">
            Click the map at the device's current location
          </div>
        )}
      </div>
    </div>
  )
}
