import { useState, useEffect, useRef, useCallback } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { MapContainer, TileLayer, Marker, Popup, useMapEvents, useMap, LayersControl } from 'react-leaflet'
import L from 'leaflet'
import api from '../api/client'
import { Search, Radio, X, Wifi, Trash2 } from 'lucide-react'
import DrawingToolbar, { WALL_TYPES, FLOOR_TYPES } from '../components/DrawingToolbar'
import { DrawingEvents, SavedWalls, SavedFloors } from '../components/MapDrawing'

// Scanner icon by health
const scannerIcon = (health) =>
  L.divIcon({
    className: '',
    html: `<div class="flex items-center justify-center w-8 h-8 rounded-full border-2 ${
      health === 'online' ? 'bg-green-500/30 border-green-400'
      : health === 'stale' ? 'bg-yellow-500/30 border-yellow-400'
      : 'bg-red-500/30 border-red-400'
    }"><svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="${
      health === 'online' ? 'text-green-400' : health === 'stale' ? 'text-yellow-400' : 'text-red-400'
    }"><path d="M4.9 19.1C1 15.2 1 8.8 4.9 4.9"/><path d="M7.8 16.2c-2.3-2.3-2.3-6.1 0-8.5"/><circle cx="12" cy="12" r="2"/><path d="M16.2 7.8c2.3 2.3 2.3 6.1 0 8.5"/><path d="M19.1 4.9C23 8.8 23 15.1 19.1 19"/></svg></div>`,
    iconSize: [32, 32],
    iconAnchor: [16, 16],
  })

// AP icon
const apIcon = L.divIcon({
  className: '',
  html: `<div class="flex items-center justify-center w-7 h-7 rounded-full border-2 bg-purple-500/30 border-purple-400"><svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="text-purple-400"><path d="M12 20h.01"/><path d="M2 8.82a15 15 0 0 1 20 0"/><path d="M5 12.859a10 10 0 0 1 14 0"/><path d="M8.5 16.429a5 5 0 0 1 7 0"/></svg></div>`,
  iconSize: [28, 28],
  iconAnchor: [14, 14],
})

// Pending placement icon
const pendingIcon = L.divIcon({
  className: '',
  html: `<div class="flex items-center justify-center w-8 h-8 rounded-full border-2 border-dashed bg-gray-500/20 border-gray-400"><svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="text-gray-400"><circle cx="12" cy="12" r="10"/><path d="M12 8v4"/><path d="M12 16h.01"/></svg></div>`,
  iconSize: [32, 32],
  iconAnchor: [16, 16],
})

function AddressSearch({ onLocate }) {
  const [address, setAddress] = useState('')
  const [searching, setSearching] = useState(false)

  const search = async (e) => {
    e.preventDefault()
    if (!address.trim()) return
    setSearching(true)
    try {
      const r = await fetch(
        `https://nominatim.openstreetmap.org/search?format=json&q=${encodeURIComponent(address)}&limit=1`,
        { headers: { 'User-Agent': 'AirScan/0.1' } }
      )
      const results = await r.json()
      if (results.length > 0) {
        onLocate(parseFloat(results[0].lat), parseFloat(results[0].lon), results[0].display_name)
      }
    } finally {
      setSearching(false)
    }
  }

  return (
    <form onSubmit={search} className="flex gap-2">
      <div className="relative flex-1">
        <Search className="absolute left-3 top-2.5 w-4 h-4 text-gray-500" />
        <input type="text" value={address} onChange={(e) => setAddress(e.target.value)}
          placeholder="Enter your address..."
          className="w-full bg-gray-900 border border-gray-700 rounded-lg pl-9 pr-3 py-2 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-blue-500" />
      </div>
      <button type="submit" disabled={searching}
        className="bg-blue-600 text-white text-sm px-4 py-2 rounded-lg hover:bg-blue-500 disabled:opacity-50">
        {searching ? '...' : 'Set Location'}
      </button>
    </form>
  )
}

function MapRecenter({ lat, lon }) {
  const map = useMap()
  useEffect(() => {
    if (lat && lon) map.setView([lat, lon], 20)
  }, [lat, lon, map])
  return null
}

function PlacementHandler({ active, onPlace }) {
  useMapEvents({
    click(e) {
      if (active) onPlace(e.latlng.lat, e.latlng.lng)
    },
  })
  return null
}

// AP search panel
function APSearchPanel({ onSelect, onClose }) {
  const [query, setQuery] = useState('')
  const { data: results } = useQuery({
    queryKey: ['apSearch', query],
    queryFn: () => api.get('/maps/aps/search', { params: { q: query } }).then((r) => r.data),
    enabled: query.length >= 2,
  })

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <p className="text-sm text-white font-medium">Search for an AP to place</p>
        <button onClick={onClose} className="text-gray-400 hover:text-white"><X className="w-4 h-4" /></button>
      </div>
      <input type="text" value={query} onChange={(e) => setQuery(e.target.value)}
        placeholder="Search by SSID, MAC, or manufacturer..." autoFocus
        className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-blue-500" />
      {results?.length > 0 && (
        <div className="max-h-48 overflow-y-auto space-y-1">
          {results.map((ap) => (
            <button key={ap.mac} onClick={() => onSelect(ap)}
              className="w-full text-left bg-gray-800 hover:bg-gray-700 rounded px-3 py-2 flex items-center justify-between">
              <div>
                <span className="text-sm text-white">{ap.ssids || ap.mac}</span>
                {ap.ssids && <span className="text-xs text-gray-500 ml-2 font-mono">{ap.mac}</span>}
              </div>
              <span className="text-xs text-gray-500">{ap.manufacturer || ''}</span>
            </button>
          ))}
        </div>
      )}
      {query.length >= 2 && results?.length === 0 && <p className="text-xs text-gray-500">No APs found</p>}
    </div>
  )
}

// Selected item info bar
function SelectionBar({ item, type, onDelete, onClose }) {
  const typeInfo = type === 'wall'
    ? WALL_TYPES.find((w) => w.value === item.wall_type)
    : FLOOR_TYPES.find((f) => f.value === item.floor_type)

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-lg p-3 flex items-center justify-between">
      <div className="flex items-center gap-3">
        <span className="w-4 h-4 rounded" style={{ background: typeInfo?.color, opacity: 0.7 }} />
        <div>
          <span className="text-sm text-white">{typeInfo?.label || type}</span>
          {item.label && <span className="text-xs text-gray-400 ml-2">{item.label}</span>}
          {type === 'wall' && <span className="text-xs text-gray-500 ml-2">({item.attenuation_db} dB)</span>}
        </div>
      </div>
      <div className="flex gap-2">
        <button onClick={onDelete} className="text-red-400 hover:text-red-300 p-1" title="Delete">
          <Trash2 className="w-4 h-4" />
        </button>
        <button onClick={onClose} className="text-gray-400 hover:text-white p-1">
          <X className="w-4 h-4" />
        </button>
      </div>
    </div>
  )
}

export default function MapView() {
  const queryClient = useQueryClient()

  // Placement state
  const [placementMode, setPlacementMode] = useState('idle') // 'idle' | 'placing_scanner' | 'search_ap' | 'placing_ap'
  const [placingItem, setPlacingItem] = useState(null)
  const [pendingPos, setPendingPos] = useState(null)
  const [zInput, setZInput] = useState('')

  // Drawing state
  const [drawMode, setDrawMode] = useState('select') // 'select' | 'wall_line' | 'wall_freehand' | 'floor_zone'
  const [drawSubType, setDrawSubType] = useState('exterior')
  const [selectedItem, setSelectedItem] = useState(null) // { type: 'wall'|'floor', ...item }
  const [drawingPointCount, setDrawingPointCount] = useState(0)
  const finishDrawingRef = useRef(null)

  // Queries
  const { data: mapConfig } = useQuery({
    queryKey: ['mapConfig'],
    queryFn: () => api.get('/maps/config').then((r) => r.data),
  })

  const { data: scanners } = useQuery({
    queryKey: ['scanners'],
    queryFn: () => api.get('/scanners/').then((r) => r.data),
    refetchInterval: 30000,
  })

  const { data: placedAPs } = useQuery({
    queryKey: ['placedAPs'],
    queryFn: () => api.get('/maps/aps').then((r) => r.data),
    refetchInterval: 30000,
  })

  const { data: walls } = useQuery({
    queryKey: ['walls'],
    queryFn: () => api.get('/maps/walls').then((r) => r.data),
  })

  const { data: floors } = useQuery({
    queryKey: ['floors'],
    queryFn: () => api.get('/maps/floors').then((r) => r.data),
  })

  // Mutations
  const saveMapConfig = useMutation({
    mutationFn: (body) => api.post('/maps/config', body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['mapConfig'] }),
  })

  const updateScanner = useMutation({
    mutationFn: ({ id, ...body }) => api.patch(`/scanners/${id}`, body),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['scanners'] }); resetPlacement() },
  })

  const placeAP = useMutation({
    mutationFn: (body) => api.post('/maps/aps/place', body),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['placedAPs'] }); resetPlacement() },
  })

  const removeAP = useMutation({
    mutationFn: (mac) => api.delete(`/maps/aps/${encodeURIComponent(mac)}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['placedAPs'] }),
  })

  const createWall = useMutation({
    mutationFn: (body) => api.post('/maps/walls', body),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ['walls'] }),
  })

  const deleteWall = useMutation({
    mutationFn: (id) => api.delete(`/maps/walls/${id}`),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['walls'] }); setSelectedItem(null) },
  })

  const createFloor = useMutation({
    mutationFn: (body) => api.post('/maps/floors', body),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ['floors'] }),
  })

  const deleteFloor = useMutation({
    mutationFn: (id) => api.delete(`/maps/floors/${id}`),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['floors'] }); setSelectedItem(null) },
  })

  // Helpers
  const resetPlacement = () => {
    setPlacementMode('idle')
    setPlacingItem(null)
    setPendingPos(null)
    setZInput('')
  }

  const center = mapConfig?.gps_anchor_lat
    ? [parseFloat(mapConfig.gps_anchor_lat), parseFloat(mapConfig.gps_anchor_lon)]
    : [39.8283, -98.5795]

  const handleLocate = (lat, lon, label) => saveMapConfig.mutate({ lat, lon, label })

  const handleMapClick = (lat, lng) => setPendingPos({ lat, lng })

  const confirmPlacement = () => {
    if (!placingItem || !pendingPos) return
    const z = parseFloat(zInput) || 0
    if (placementMode === 'placing_scanner') {
      updateScanner.mutate({ id: placingItem.id, x_pos: pendingPos.lat, y_pos: pendingPos.lng, z_pos: z })
    } else if (placementMode === 'placing_ap') {
      placeAP.mutate({ mac: placingItem.mac, lat: pendingPos.lat, lon: pendingPos.lng, z_pos: z })
    }
  }

  const handleFinishWall = (points) => {
    createWall.mutate({ wall_type: drawSubType, points })
  }

  const handleFinishFloor = (polygon) => {
    createFloor.mutate({ floor_type: drawSubType, polygon })
  }

  const handleSelectDrawn = (item, type) => {
    if (drawMode === 'select') {
      setSelectedItem({ ...item, _type: type })
    }
  }

  const placedScanners = (scanners || []).filter((s) => s.x_pos != null && s.y_pos != null)
  const unplacedScanners = (scanners || []).filter((s) => s.x_pos == null || s.y_pos == null)
  const isPlacing = placementMode === 'placing_scanner' || placementMode === 'placing_ap'
  const isDrawing = drawMode !== 'select'
  const itemLabel = placingItem?.label || placingItem?.hostname || placingItem?.ssids || placingItem?.mac || ''

  return (
    <div className="p-6 space-y-3 h-full flex flex-col">
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-bold text-white">Map</h2>
        <div className="flex gap-2">
          {placementMode === 'idle' && !isDrawing && (
            <button onClick={() => { setPlacementMode('search_ap'); setDrawMode('select') }}
              className="flex items-center gap-2 bg-purple-600 text-white text-sm px-4 py-2 rounded-lg hover:bg-purple-500">
              <Wifi className="w-4 h-4" /> Place AP
            </button>
          )}
        </div>
      </div>

      <AddressSearch onLocate={handleLocate} />

      {/* Drawing toolbar */}
      {placementMode === 'idle' && (
        <DrawingToolbar
          mode={drawMode}
          onModeChange={(m) => { setDrawMode(m); setSelectedItem(null) }}
          subType={drawSubType}
          onSubTypeChange={setDrawSubType}
          pointCount={drawingPointCount}
          onDone={() => { if (finishDrawingRef.current) finishDrawingRef.current() }}
        />
      )}

      {/* Selected item info */}
      {selectedItem && !isDrawing && (
        <SelectionBar
          item={selectedItem}
          type={selectedItem._type}
          onDelete={() => {
            if (selectedItem._type === 'wall') deleteWall.mutate(selectedItem.id)
            else deleteFloor.mutate(selectedItem.id)
          }}
          onClose={() => setSelectedItem(null)}
        />
      )}

      {/* Placement controls */}
      {placementMode !== 'idle' && (
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
          {placementMode === 'search_ap' && (
            <APSearchPanel
              onSelect={(ap) => { setPlacementMode('placing_ap'); setPlacingItem(ap); setPendingPos(null); setZInput('') }}
              onClose={resetPlacement}
            />
          )}

          {isPlacing && (
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <p className="text-sm text-white">
                  Placing {placementMode === 'placing_scanner' ? 'scanner' : 'AP'}: <span className="font-bold text-blue-400">{itemLabel}</span>
                  {pendingPos ? ' — confirm to save' : ' — click on the map'}
                </p>
                <button onClick={resetPlacement} className="text-gray-400 hover:text-white"><X className="w-4 h-4" /></button>
              </div>
              {pendingPos && (
                <div className="flex items-center gap-3">
                  <span className="text-xs text-gray-400 font-mono">{pendingPos.lat.toFixed(6)}, {pendingPos.lng.toFixed(6)}</span>
                  <label className="text-sm text-gray-400">Height (ft):</label>
                  <input type="number" value={zInput} onChange={(e) => setZInput(e.target.value)} placeholder="0"
                    className="bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-sm text-white w-24 focus:outline-none focus:border-blue-500" />
                  <button onClick={confirmPlacement}
                    className="bg-green-600 text-white text-sm px-4 py-1.5 rounded-lg hover:bg-green-500">Confirm</button>
                </div>
              )}
            </div>
          )}

          {placementMode === 'idle' && unplacedScanners.length > 0 && (
            <div className="space-y-2">
              <p className="text-xs text-gray-500">Unplaced scanners:</p>
              <div className="flex flex-wrap gap-2">
                {unplacedScanners.map((s) => (
                  <button key={s.id}
                    onClick={() => { setPlacementMode('placing_scanner'); setPlacingItem(s); setPendingPos(null); setZInput(''); setDrawMode('select') }}
                    className="flex items-center gap-2 bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white hover:border-blue-500">
                    <Radio className="w-4 h-4 text-gray-400" />{s.label || s.hostname}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Unplaced scanners when idle and no placement panel */}
      {placementMode === 'idle' && !isDrawing && unplacedScanners.length > 0 && (
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-3">
          <div className="flex flex-wrap gap-2 items-center">
            <span className="text-xs text-gray-500">Unplaced:</span>
            {unplacedScanners.map((s) => (
              <button key={s.id}
                onClick={() => { setPlacementMode('placing_scanner'); setPlacingItem(s); setPendingPos(null); setZInput(''); setDrawMode('select') }}
                className="flex items-center gap-2 bg-gray-800 border border-gray-700 rounded-lg px-2 py-1.5 text-xs text-white hover:border-blue-500">
                <Radio className="w-3 h-3 text-gray-400" />{s.label || s.hostname}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Map */}
      <div className="flex-1 min-h-[500px] rounded-lg overflow-hidden border border-gray-800">
        <MapContainer center={center} zoom={mapConfig?.gps_anchor_lat ? 20 : 4}
          maxZoom={22} doubleClickZoom={false}
          className="h-full w-full" style={{ background: '#1a1a2e' }}>
          <LayersControl position="topright">
            <LayersControl.BaseLayer name="Satellite" checked>
              <TileLayer attribution='Imagery &copy; Esri'
                url="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
                maxZoom={22} maxNativeZoom={20} />
            </LayersControl.BaseLayer>
            <LayersControl.BaseLayer name="Street">
              <TileLayer attribution='&copy; OpenStreetMap'
                url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
                maxZoom={22} maxNativeZoom={19} />
            </LayersControl.BaseLayer>
            <LayersControl.BaseLayer name="Google Satellite">
              <TileLayer url="https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}"
                maxZoom={22} maxNativeZoom={21} />
            </LayersControl.BaseLayer>
          </LayersControl>

          <MapRecenter
            lat={mapConfig?.gps_anchor_lat ? parseFloat(mapConfig.gps_anchor_lat) : null}
            lon={mapConfig?.gps_anchor_lon ? parseFloat(mapConfig.gps_anchor_lon) : null} />

          {/* Drawing handler — only active in draw modes */}
          {isDrawing && (
            <DrawingEvents
              drawMode={drawMode}
              subType={drawSubType}
              onFinishWall={handleFinishWall}
              onFinishFloor={handleFinishFloor}
              onPointCountChange={setDrawingPointCount}
              finishRef={finishDrawingRef}
            />
          )}

          {/* Placement handler — only active when placing */}
          {isPlacing && <PlacementHandler active={true} onPlace={handleMapClick} />}

          {/* Saved floor zones (render first so walls draw on top) */}
          <SavedFloors
            floors={floors || []}
            selectedId={selectedItem?._type === 'floor' ? selectedItem.id : null}
            onSelect={(f) => handleSelectDrawn(f, 'floor')}
          />

          {/* Saved walls */}
          <SavedWalls
            walls={walls || []}
            selectedId={selectedItem?._type === 'wall' ? selectedItem.id : null}
            onSelect={(w) => handleSelectDrawn(w, 'wall')}
          />

          {/* Placed scanners */}
          {placedScanners.map((s) => (
            <Marker key={`scanner-${s.id}`} position={[parseFloat(s.x_pos), parseFloat(s.y_pos)]}
              icon={scannerIcon(s.health)}>
              <Popup>
                <div className="text-sm space-y-1">
                  <p className="font-bold">{s.label || s.hostname}</p>
                  <p className="text-xs">{s.hostname}</p>
                  <p className="text-xs">Height: {s.z_pos ? `${parseFloat(s.z_pos)} ft` : 'ground level'}</p>
                  <p className="text-xs">Status: {s.health}</p>
                  {s.recent_obs !== undefined && <p className="text-xs">Obs (10m): {s.recent_obs}</p>}
                  <button onClick={() => { setPlacementMode('placing_scanner'); setPlacingItem(s); setPendingPos(null); setDrawMode('select') }}
                    className="text-xs text-blue-400 hover:underline mt-1">Reposition</button>
                </div>
              </Popup>
            </Marker>
          ))}

          {/* Placed APs */}
          {(placedAPs || []).map((ap) => (
            <Marker key={`ap-${ap.mac}`} position={[parseFloat(ap.lat), parseFloat(ap.lon)]} icon={apIcon}>
              <Popup>
                <div className="text-sm space-y-1">
                  <p className="font-bold">{ap.ssids || 'Hidden'}</p>
                  <p className="text-xs font-mono">{ap.mac}</p>
                  {ap.manufacturer && <p className="text-xs">{ap.manufacturer}</p>}
                  <p className="text-xs">Height: {ap.z_pos ? `${parseFloat(ap.z_pos)} ft` : 'ground level'}</p>
                  <div className="flex gap-2 mt-1">
                    <button onClick={() => { setPlacementMode('placing_ap'); setPlacingItem(ap); setPendingPos(null); setDrawMode('select') }}
                      className="text-xs text-blue-400 hover:underline">Reposition</button>
                    <button onClick={() => removeAP.mutate(ap.mac)}
                      className="text-xs text-red-400 hover:underline">Remove</button>
                  </div>
                </div>
              </Popup>
            </Marker>
          ))}

          {/* Pending placement marker */}
          {pendingPos && (
            <Marker position={[pendingPos.lat, pendingPos.lng]} icon={pendingIcon}>
              <Popup><p className="text-sm">New position for {itemLabel}</p></Popup>
            </Marker>
          )}
        </MapContainer>
      </div>

      {/* Legend */}
      <div className="flex flex-wrap gap-4 text-xs text-gray-500">
        <div className="flex items-center gap-1"><span className="w-4 h-1 bg-red-500 rounded" /> Exterior wall</div>
        <div className="flex items-center gap-1"><span className="w-4 h-1 bg-amber-500 rounded" /> Interior wall</div>
        <div className="flex items-center gap-1"><span className="w-4 h-1 bg-orange-500 rounded" /> Addition wall</div>
        <div className="flex items-center gap-1"><span className="w-3 h-3 bg-green-500/30 rounded-full border border-green-400" /> Scanner</div>
        <div className="flex items-center gap-1"><span className="w-3 h-3 bg-purple-500/30 rounded-full border border-purple-400" /> AP</div>
      </div>
    </div>
  )
}
