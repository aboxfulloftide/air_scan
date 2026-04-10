import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'
import { MapContainer, TileLayer, Marker, Popup, Polyline, useMapEvents, useMap, LayersControl, LayerGroup, CircleMarker, Tooltip } from 'react-leaflet'
import L from 'leaflet'
import api from '../api/client'
import { Search, Radio, X, Wifi, Trash2, MonitorSmartphone } from 'lucide-react'
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

const fixedDeviceIcon = L.divIcon({
  className: '',
  html: `<div class="flex items-center justify-center w-7 h-7 rounded-lg border-2 bg-cyan-500/25 border-cyan-300"><svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="text-cyan-300"><rect width="20" height="14" x="2" y="3" rx="2"/><line x1="8" x2="16" y1="21" y2="21"/><line x1="12" x2="12" y1="17" y2="21"/></svg></div>`,
  iconSize: [28, 28],
  iconAnchor: [14, 14],
})

// Spread overlapping items in a circle so they're individually clickable
// Returns a Map<originalKey, [offsetLat, offsetLon]>
// spreadMeters controls how far apart items are pushed at the given zoom
function spreadOverlaps(items, latFn, lonFn, zoom) {
  // Grid cell size in degrees — at zoom 20 a marker covers ~2m, so group within ~4m
  // 0.00004 deg ≈ 4.4m at mid-latitudes; scale with zoom
  const cellDeg = 0.00004 * Math.pow(2, 20 - Math.min(zoom, 22))
  // Spread radius: how far apart to push items in a group
  const spreadDeg = 0.000025 * Math.pow(2, 20 - Math.min(zoom, 22))

  const groups = new Map()
  for (let i = 0; i < items.length; i++) {
    const lat = latFn(items[i]), lon = lonFn(items[i])
    // Snap to grid cell
    const gLat = Math.round(lat / cellDeg)
    const gLon = Math.round(lon / cellDeg)
    const key = `${gLat},${gLon}`
    if (!groups.has(key)) groups.set(key, [])
    groups.get(key).push(i)
  }
  const offsets = new Array(items.length)
  for (const indices of groups.values()) {
    if (indices.length === 1) {
      offsets[indices[0]] = [0, 0]
    } else {
      const n = indices.length
      // Use concentric rings for large groups
      const perRing = Math.max(6, n)
      for (let j = 0; j < n; j++) {
        const ring = Math.floor(j / perRing)
        const posInRing = j % perRing
        const ringCount = Math.min(perRing, n - ring * perRing)
        const r = spreadDeg * (ring + 1)
        const angle = (2 * Math.PI * posInRing) / ringCount
        offsets[indices[j]] = [Math.cos(angle) * r, Math.sin(angle) * r]
      }
    }
  }
  return offsets
}

// Confidence → CircleMarker style
const confidenceStyle = (confidence) => {
  const conf = parseFloat(confidence) || 0
  if (conf >= 60) return { fillColor: '#3b82f6', color: '#93c5fd' }
  if (conf >= 30) return { fillColor: '#f59e0b', color: '#fcd34d' }
  return { fillColor: '#6b7280', color: '#9ca3af' }
}

// Highlight ring for searched device
const highlightIcon = L.divIcon({
  className: '',
  html: `<div style="position:relative;width:48px;height:48px">
    <div style="position:absolute;inset:0;border-radius:50%;border:3px solid #f59e0b;background:rgba(245,158,11,0.18);animation:pulse-ring 1.5s ease-in-out infinite"></div>
    <div style="position:absolute;inset:-12px;border-radius:50%;border:2px solid rgba(245,158,11,0.4);animation:pulse-ring 1.5s ease-in-out infinite 0.3s"></div>
  </div>`,
  iconSize: [48, 48],
  iconAnchor: [24, 24],
})

// Pending placement icon
const pendingIcon = L.divIcon({
  className: '',
  html: `<div class="flex items-center justify-center w-8 h-8 rounded-full border-2 border-dashed bg-gray-500/20 border-gray-400"><svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="text-gray-400"><circle cx="12" cy="12" r="10"/><path d="M12 8v4"/><path d="M12 16h.01"/></svg></div>`,
  iconSize: [32, 32],
  iconAnchor: [16, 16],
})

function DeviceMapSearch({ onSelect }) {
  const [query, setQuery] = useState('')
  const { data: results } = useQuery({
    queryKey: ['mapDeviceSearch', query],
    queryFn: () => api.get('/maps/devices/search', { params: { q: query } }).then((r) => r.data),
    enabled: query.length >= 2,
  })

  return (
    <div className="relative">
      <div className="relative">
        <Search className="absolute left-3 top-2.5 w-4 h-4 text-gray-500" />
        <input type="text" value={query} onChange={(e) => setQuery(e.target.value)}
          placeholder="Search devices by name, MAC, owner..."
          className="w-full bg-gray-900 border border-gray-700 rounded-lg pl-9 pr-3 py-2 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-blue-500" />
      </div>
      {query.length >= 2 && results?.length > 0 && (
        <div className="absolute z-[1000] mt-1 w-full bg-gray-900 border border-gray-700 rounded-lg max-h-56 overflow-y-auto shadow-lg">
          {results.map((d) => (
            <button key={d.mac} onClick={() => { onSelect(d.mac); setQuery('') }}
              className="w-full text-left px-3 py-2 hover:bg-gray-800 flex items-center justify-between">
              <div>
                <span className="text-sm text-white">{d.known_label || d.ssids || d.mac}</span>
                {(d.known_label || d.ssids) && <span className="text-xs text-gray-500 ml-2 font-mono">{d.mac}</span>}
                {(d.owner || d.manufacturer) && <div className="text-xs text-gray-500">{d.owner || d.manufacturer}</div>}
              </div>
              <div className="text-xs text-gray-500">{d.device_type}</div>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

function MapRecenter({ lat, lon, allPoints, focusPoint }) {
  const map = useMap()
  const initialDone = useRef(false)
  const lastFocus = useRef(null)

  // Focus on a specific device (from search or URL param) — always respond to changes
  useEffect(() => {
    if (!focusPoint) return
    const key = focusPoint.join(',')
    if (key === lastFocus.current) return
    lastFocus.current = key
    initialDone.current = true
    map.setView(focusPoint, 21)
  }, [focusPoint, map])

  // Initial load: fit bounds or use config anchor
  useEffect(() => {
    if (initialDone.current) return
    if (allPoints && allPoints.length >= 2) {
      map.fitBounds(allPoints.map((p) => [p[0], p[1]]), { padding: [40, 40], maxZoom: 20 })
      initialDone.current = true
    } else if (lat && lon) {
      map.setView([lat, lon], 20)
    }
  }, [lat, lon, allPoints, map])

  return null
}

function ZoomTracker({ onZoomChange }) {
  const map = useMap()
  useMapEvents({ zoomend: () => onZoomChange(map.getZoom()) })
  useEffect(() => { onZoomChange(map.getZoom()) }, [map, onZoomChange])
  return null
}

const LAYER_STORAGE_KEY = 'mapView.visibleLayers'
const ALL_LAYERS = ['Scanners', 'Placed APs', 'Fixed Devices', 'Detected Clients', 'Detected APs']

function getStoredLayers() {
  try { const s = localStorage.getItem(LAYER_STORAGE_KEY); return s ? JSON.parse(s) : null }
  catch { return null }
}

function LayerPersist({ onLayerChange }) {
  const map = useMap()
  useEffect(() => {
    const onAdd = (e) => onLayerChange(e.name, true)
    const onRemove = (e) => onLayerChange(e.name, false)
    map.on('overlayadd', onAdd)
    map.on('overlayremove', onRemove)
    return () => { map.off('overlayadd', onAdd); map.off('overlayremove', onRemove) }
  }, [map, onLayerChange])
  return null
}

const TILE_PROVIDERS = {
  google:  { label: 'Google',  url: 'https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}', maxNative: 21, attr: 'Imagery &copy; Google' },
  bing:    { label: 'Bing',    url: 'https://ecn.t{s}.tiles.virtualearth.net/tiles/a{q}?g=1&n=z', maxNative: 20, attr: 'Imagery &copy; Microsoft', subdomains: '0123', isBing: true },
}

// Bing uses a quadkey tile addressing scheme
function BingTileLayer({ provider }) {
  const map = useMap()
  const layerRef = useRef(null)

  useEffect(() => {
    const BingLayer = L.TileLayer.extend({
      getTileUrl(coords) {
        const z = coords.z
        let quadkey = ''
        for (let i = z; i > 0; i--) {
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
  // Satellite imagery gets blurry past maxNativeZoom; switch to street tiles
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

function FloorPane() {
  const map = useMap()
  useEffect(() => {
    if (!map.getPane('floorPane')) {
      map.createPane('floorPane')
      map.getPane('floorPane').style.zIndex = 350
    }
  }, [map])
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

function DeviceSearchPanel({ title, placeholder, queryKeyPrefix, endpoint, emptyLabel, onSelect, onClose }) {
  const [query, setQuery] = useState('')
  const { data: results } = useQuery({
    queryKey: [queryKeyPrefix, query],
    queryFn: () => api.get(endpoint, { params: { q: query } }).then((r) => r.data),
    enabled: query.length >= 2,
  })

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <p className="text-sm text-white font-medium">{title}</p>
        <button onClick={onClose} className="text-gray-400 hover:text-white"><X className="w-4 h-4" /></button>
      </div>
      <input type="text" value={query} onChange={(e) => setQuery(e.target.value)}
        placeholder={placeholder} autoFocus
        className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-blue-500" />
      {results?.length > 0 && (
        <div className="max-h-48 overflow-y-auto space-y-1">
          {results.map((device) => (
            <button key={device.mac} onClick={() => onSelect(device)}
              className="w-full text-left bg-gray-800 hover:bg-gray-700 rounded px-3 py-2 flex items-center justify-between">
              <div>
                <span className="text-sm text-white">{device.known_label || device.label || device.ssids || device.mac}</span>
                {(device.known_label || device.label || device.ssids) && (
                  <span className="text-xs text-gray-500 ml-2 font-mono">{device.mac}</span>
                )}
                {(device.owner || device.manufacturer) && (
                  <div className="text-xs text-gray-500 mt-0.5">{device.owner || device.manufacturer}</div>
                )}
              </div>
              <div className="text-right">
                <div className="text-xs text-gray-500">{device.device_type || ''}</div>
                {device.is_fixed ? <div className="text-xs text-cyan-400">Pinned</div> : null}
              </div>
            </button>
          ))}
        </div>
      )}
      {query.length >= 2 && results?.length === 0 && <p className="text-xs text-gray-500">{emptyLabel}</p>}
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
  const [searchParams] = useSearchParams()
  const [searchedMac, setSearchedMac] = useState(null)
  const [searchedPos, setSearchedPos] = useState(null)
  const focusMac = searchedMac || searchParams.get('device')

  // Placement state
  const [placementMode, setPlacementMode] = useState('idle') // 'idle' | 'placing_scanner' | 'search_ap' | 'placing_ap' | 'search_fixed_device' | 'placing_fixed_device'
  const [placingItem, setPlacingItem] = useState(null)
  const [pendingPos, setPendingPos] = useState(null)
  const [zInput, setZInput] = useState('')

  // Zoom tracking for auto tile switch + label visibility
  const [zoom, setZoom] = useState(20)
  const handleZoomChange = useCallback((z) => setZoom(z), [])
  const [tileProvider, setTileProvider] = useState('google')
  const [timeRange, setTimeRange] = useState(() => {
    const stored = localStorage.getItem('map_time_range')
    return stored ? parseFloat(stored) : 3
  })

  // Layer visibility (persisted to localStorage)
  const [visibleLayers, setVisibleLayers] = useState(() => {
    const stored = getStoredLayers()
    return stored || Object.fromEntries(ALL_LAYERS.map((l) => [l, true]))
  })
  const handleLayerChange = useCallback((name, visible) => {
    setVisibleLayers((prev) => {
      const next = { ...prev, [name]: visible }
      localStorage.setItem(LAYER_STORAGE_KEY, JSON.stringify(next))
      return next
    })
  }, [])

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

  const { data: fixedDevices } = useQuery({
    queryKey: ['fixedDevices'],
    queryFn: () => api.get('/maps/devices/fixed').then((r) => r.data),
    refetchInterval: 30000,
  })

  const { data: computedPositions } = useQuery({
    queryKey: ['computedPositions', timeRange],
    queryFn: () => api.get('/maps/positions', { params: { hours: timeRange } }).then((r) => r.data),
    refetchInterval: 10000,
  })

  const { data: walls } = useQuery({
    queryKey: ['walls'],
    queryFn: () => api.get('/maps/walls').then((r) => r.data),
  })

  const { data: floors } = useQuery({
    queryKey: ['floors'],
    queryFn: () => api.get('/maps/floors').then((r) => r.data),
  })

  const scannerList = Array.isArray(scanners) ? scanners : []
  const placedAPList = Array.isArray(placedAPs) ? placedAPs : []
  const fixedDeviceList = Array.isArray(fixedDevices) ? fixedDevices : []
  const computedList = Array.isArray(computedPositions) ? computedPositions : []
  const wallList = Array.isArray(walls) ? walls : []
  const floorList = Array.isArray(floors) ? floors : []

  // Compute offsets for overlapping items so stacked markers fan out
  const computedOffsets = useMemo(() =>
    spreadOverlaps(computedList, (d) => parseFloat(d.lat), (d) => parseFloat(d.lon), zoom),
    [computedList, zoom])
  const apOffsets = useMemo(() =>
    spreadOverlaps(placedAPList, (d) => parseFloat(d.lat), (d) => parseFloat(d.lon), zoom),
    [placedAPList, zoom])
  const fixedOffsets = useMemo(() =>
    spreadOverlaps(fixedDeviceList, (d) => parseFloat(d.lat), (d) => parseFloat(d.lon), zoom),
    [fixedDeviceList, zoom])

  // Mutations
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

  const placeFixedDevice = useMutation({
    mutationFn: (body) => api.post('/maps/devices/fixed', body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['fixedDevices'] })
      queryClient.invalidateQueries({ queryKey: ['devices'] })
      resetPlacement()
    },
  })

  const removeFixedDevice = useMutation({
    mutationFn: (mac) => api.delete(`/maps/devices/fixed/${encodeURIComponent(mac)}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['fixedDevices'] })
      queryClient.invalidateQueries({ queryKey: ['devices'] })
    },
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

  const handleDeviceSearch = async (mac) => {
    setSearchedMac(mac)
    try {
      const r = await api.get(`/maps/positions/${encodeURIComponent(mac)}`)
      if (r.data && r.data.lat != null) {
        setSearchedPos([parseFloat(r.data.lat), parseFloat(r.data.lon)])
      } else {
        setSearchedPos(null)
      }
    } catch {
      setSearchedPos(null)
    }
  }

  const handleMapClick = (lat, lng) => setPendingPos({ lat, lng })

  const confirmPlacement = () => {
    if (!placingItem || !pendingPos) return
    const z = parseFloat(zInput) || 0
    if (placementMode === 'placing_scanner') {
      updateScanner.mutate({ id: placingItem.id, x_pos: pendingPos.lat, y_pos: pendingPos.lng, z_pos: z })
    } else if (placementMode === 'placing_ap') {
      placeAP.mutate({ mac: placingItem.mac, lat: pendingPos.lat, lon: pendingPos.lng, z_pos: z })
    } else if (placementMode === 'placing_fixed_device') {
      placeFixedDevice.mutate({
        mac: placingItem.mac,
        port_scan_host_id: placingItem.port_scan_host_id,
        lat: pendingPos.lat,
        lon: pendingPos.lng,
        z_pos: z,
      })
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

  const placedScanners = scannerList.filter((s) => s.x_pos != null && s.y_pos != null)
  const unplacedScanners = scannerList.filter((s) => s.x_pos == null || s.y_pos == null)

  // Collect all marker positions for auto-fit on load
  const allPoints = useMemo(() => {
    const pts = []
    for (const s of placedScanners) pts.push([parseFloat(s.x_pos), parseFloat(s.y_pos)])
    for (const a of placedAPList) pts.push([parseFloat(a.lat), parseFloat(a.lon)])
    for (const d of fixedDeviceList) pts.push([parseFloat(d.lat), parseFloat(d.lon)])
    for (const d of computedList) pts.push([parseFloat(d.lat), parseFloat(d.lon)])
    return pts
  }, [placedScanners, placedAPList, fixedDeviceList, computedList])

  // Find focus device position — prefer direct lookup, fall back to loaded lists
  const focusPoint = useMemo(() => {
    if (!focusMac) return null
    if (searchedPos) return searchedPos
    const fixed = fixedDeviceList.find((d) => d.mac === focusMac)
    if (fixed) return [parseFloat(fixed.lat), parseFloat(fixed.lon)]
    const ap = placedAPList.find((a) => a.mac === focusMac)
    if (ap) return [parseFloat(ap.lat), parseFloat(ap.lon)]
    const computed = computedList.find((d) => d.mac === focusMac)
    if (computed) return [parseFloat(computed.lat), parseFloat(computed.lon)]
    return null
  }, [focusMac, searchedPos, fixedDeviceList, placedAPList, computedList])

  const isPlacing = placementMode === 'placing_scanner' || placementMode === 'placing_ap' || placementMode === 'placing_fixed_device'
  const isDrawing = drawMode !== 'select'
  const itemLabel = placingItem?.known_label || placingItem?.label || placingItem?.hostname || placingItem?.ssids || placingItem?.mac || ''

  return (
    <div className="p-6 space-y-3 h-full flex flex-col">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <h2 className="text-xl font-bold text-white">Map</h2>
          <div className="flex bg-gray-800 rounded-lg p-0.5 text-xs">
            {Object.entries(TILE_PROVIDERS).map(([key, p]) => (
              <button key={key} onClick={() => setTileProvider(key)}
                className={`px-2.5 py-1 rounded-md transition-colors ${tileProvider === key ? 'bg-gray-600 text-white' : 'text-gray-400 hover:text-gray-200'}`}>
                {p.label}
              </button>
            ))}
          </div>
          <div className="flex bg-gray-800 rounded-lg p-0.5 text-xs">
            {[
              { value: 1, label: '1h' },
              { value: 3, label: '3h' },
              { value: 12, label: '12h' },
              { value: 24, label: '24h' },
              { value: 0, label: 'All' },
            ].map((opt) => (
              <button key={opt.value} onClick={() => { setTimeRange(opt.value); localStorage.setItem('map_time_range', String(opt.value)) }}
                className={`px-2.5 py-1 rounded-md transition-colors ${timeRange === opt.value ? 'bg-gray-600 text-white' : 'text-gray-400 hover:text-gray-200'}`}>
                {opt.label}
              </button>
            ))}
          </div>
        </div>
        <div className="flex gap-2">
          {placementMode === 'idle' && !isDrawing && (
            <>
              <button onClick={() => { setPlacementMode('search_fixed_device'); setDrawMode('select') }}
                className="flex items-center gap-2 bg-cyan-600 text-white text-sm px-4 py-2 rounded-lg hover:bg-cyan-500">
                <MonitorSmartphone className="w-4 h-4" /> Place Fixed Device
              </button>
              <button onClick={() => { setPlacementMode('search_ap'); setDrawMode('select') }}
                className="flex items-center gap-2 bg-purple-600 text-white text-sm px-4 py-2 rounded-lg hover:bg-purple-500">
                <Wifi className="w-4 h-4" /> Place AP
              </button>
            </>
          )}
        </div>
      </div>

      <DeviceMapSearch onSelect={handleDeviceSearch} />

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
            <DeviceSearchPanel
              title="Search for an AP to place"
              placeholder="Search by SSID, MAC, or manufacturer..."
              queryKeyPrefix="apSearch"
              endpoint="/maps/aps/search"
              emptyLabel="No APs found"
              onSelect={(ap) => { setPlacementMode('placing_ap'); setPlacingItem(ap); setPendingPos(null); setZInput('') }}
              onClose={resetPlacement}
            />
          )}

          {placementMode === 'search_fixed_device' && (
            <DeviceSearchPanel
              title="Search for a fixed Wi-Fi device"
              placeholder="Search by label, owner, SSID, MAC, or manufacturer..."
              queryKeyPrefix="deviceSearch"
              endpoint="/maps/devices/search"
              emptyLabel="No matching devices found"
              onSelect={(device) => { setPlacementMode('placing_fixed_device'); setPlacingItem(device); setPendingPos(null); setZInput('') }}
              onClose={resetPlacement}
            />
          )}

          {isPlacing && (
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <p className="text-sm text-white">
                  Placing {placementMode === 'placing_scanner' ? 'scanner' : placementMode === 'placing_ap' ? 'AP' : 'fixed device'}: <span className="font-bold text-blue-400">{itemLabel}</span>
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
                    onClick={() => { setPlacementMode('placing_scanner'); setPlacingItem(s); setPendingPos(null); setZInput(s.z_pos ?? ''); setDrawMode('select') }}
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
                onClick={() => { setPlacementMode('placing_scanner'); setPlacingItem(s); setPendingPos(null); setZInput(s.z_pos ?? ''); setDrawMode('select') }}
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
          <AutoTileLayer zoom={zoom} provider={tileProvider} />
          <ZoomTracker onZoomChange={handleZoomChange} />

          <LayerPersist onLayerChange={handleLayerChange} />
          <LayersControl position="topright">
            <LayersControl.Overlay name="Scanners" checked={visibleLayers['Scanners'] !== false}>
              <LayerGroup>
                {placedScanners.map((s) => (
                  <Marker key={`scanner-${s.id}`} position={[parseFloat(s.x_pos), parseFloat(s.y_pos)]}
                    icon={scannerIcon(s.health)} interactive={!isPlacing}>
                    {!isPlacing && (
                      <Popup>
                        <div className="text-sm space-y-1">
                          <p className="font-bold">{s.label || s.hostname}</p>
                          <p className="text-xs">{s.hostname}</p>
                          <p className="text-xs">Height: {s.z_pos ? `${parseFloat(s.z_pos)} ft` : 'ground level'}</p>
                          <p className="text-xs">Status: {s.health}</p>
                          {s.recent_obs !== undefined && <p className="text-xs">Obs (10m): {s.recent_obs}</p>}
                          {s.rssi_offset != null && parseFloat(s.rssi_offset) !== 0 && (
                            <p className="text-xs">RSSI offset: {parseFloat(s.rssi_offset) > 0 ? '+' : ''}{parseFloat(s.rssi_offset).toFixed(1)} dB ({s.calibration_samples} anchors)</p>
                          )}
                          <button onClick={() => { setPlacementMode('placing_scanner'); setPlacingItem(s); setPendingPos(null); setZInput(s.z_pos ?? ''); setDrawMode('select') }}
                            className="text-xs text-blue-400 hover:underline mt-1">Reposition</button>
                        </div>
                      </Popup>
                    )}
                  </Marker>
                ))}
              </LayerGroup>
            </LayersControl.Overlay>

            <LayersControl.Overlay name="Placed APs" checked={visibleLayers['Placed APs'] !== false}>
              <LayerGroup>
                {placedAPList.map((ap, idx) => (
                  <Marker key={`ap-${ap.mac}`} position={[parseFloat(ap.lat) + (apOffsets[idx]?.[0] || 0), parseFloat(ap.lon) + (apOffsets[idx]?.[1] || 0)]} icon={apIcon}
                    interactive={!isPlacing}>
                    {!isPlacing && (
                      <Popup>
                        <div className="text-sm space-y-1">
                          <p className="font-bold">{ap.ssids || 'Hidden'}</p>
                          <p className="text-xs font-mono">{ap.mac}</p>
                          {ap.manufacturer && <p className="text-xs">{ap.manufacturer}</p>}
                          <p className="text-xs">Height: {ap.z_pos ? `${parseFloat(ap.z_pos)} ft` : 'ground level'}</p>
                          <div className="flex gap-2 mt-1">
                            <button onClick={() => { setPlacementMode('placing_ap'); setPlacingItem(ap); setPendingPos(null); setZInput(ap.z_pos ?? ''); setDrawMode('select') }}
                              className="text-xs text-blue-400 hover:underline">Reposition</button>
                            <button onClick={() => removeAP.mutate(ap.mac)}
                              className="text-xs text-red-400 hover:underline">Remove</button>
                          </div>
                        </div>
                      </Popup>
                    )}
                  </Marker>
                ))}
              </LayerGroup>
            </LayersControl.Overlay>

            <LayersControl.Overlay name="Fixed Devices" checked={visibleLayers['Fixed Devices'] !== false}>
              <LayerGroup>
                {fixedDeviceList.map((device, idx) => (
                  <Marker key={`fixed-${device.mac}`} position={[parseFloat(device.lat) + (fixedOffsets[idx]?.[0] || 0), parseFloat(device.lon) + (fixedOffsets[idx]?.[1] || 0)]} icon={fixedDeviceIcon}
                    interactive={!isPlacing}>
                    {!isPlacing && (
                      <Popup>
                        <div className="text-sm space-y-1">
                          <p className="font-bold">{device.label || device.ssids || device.mac}</p>
                          <p className="text-xs font-mono">{device.mac}</p>
                          <p className="text-xs">{device.device_type} fixed anchor</p>
                          {device.owner && <p className="text-xs">Owner: {device.owner}</p>}
                          {device.manufacturer && <p className="text-xs">{device.manufacturer}</p>}
                          <p className="text-xs">Height: {device.z_pos ? `${parseFloat(device.z_pos)} ft` : 'ground level'}</p>
                          <div className="flex gap-2 mt-1">
                            <button onClick={() => { setPlacementMode('placing_fixed_device'); setPlacingItem(device); setPendingPos(null); setZInput(device.z_pos ?? ''); setDrawMode('select') }}
                              className="text-xs text-blue-400 hover:underline">Reposition</button>
                            <button onClick={() => removeFixedDevice.mutate(device.mac)}
                              className="text-xs text-red-400 hover:underline">Remove</button>
                          </div>
                        </div>
                      </Popup>
                    )}
                  </Marker>
                ))}
              </LayerGroup>
            </LayersControl.Overlay>

            <LayersControl.Overlay name="Detected Clients" checked={visibleLayers['Detected Clients'] !== false}>
              <LayerGroup>
                {computedList.map((d, idx) => {
                  if (d.device_type !== 'Client') return null
                  const off = computedOffsets[idx] || [0, 0]
                  const style = confidenceStyle(d.confidence)
                  const name = d.known_label || d.owner || ''
                  const isFocused = focusMac && d.mac === focusMac
                  return (
                    <CircleMarker key={`pos-${d.mac}`}
                      center={[parseFloat(d.lat) + off[0], parseFloat(d.lon) + off[1]]}
                      radius={isFocused ? 12 : zoom >= 20 ? 7 : 5}
                      pathOptions={isFocused
                        ? { fillColor: '#f59e0b', fillOpacity: 0.9, color: '#fbbf24', weight: 3, opacity: 1 }
                        : { fillColor: style.fillColor, fillOpacity: 0.7, color: style.color, weight: 2, opacity: 0.9 }}
                      interactive={!isPlacing}>
                      {!isPlacing && (
                        <>
                          <Tooltip direction="right" offset={[8, 0]}
                            permanent={zoom >= 21 && !!name}
                            className="device-tooltip">
                            {name || d.manufacturer || d.mac}
                          </Tooltip>
                          <Popup>
                            <div className="text-sm space-y-1">
                              <p className="font-bold">{d.known_label || d.manufacturer || d.mac}</p>
                              <p className="text-xs font-mono">{d.mac}</p>
                              {d.owner && <p className="text-xs">Owner: {d.owner}</p>}
                              {d.manufacturer && <p className="text-xs">{d.manufacturer}</p>}
                              <p className="text-xs">Confidence: {parseFloat(d.confidence).toFixed(0)}% ({d.method}, {d.scanner_count} scanners)</p>
                              <p className="text-xs">Status: {d.status || 'unknown'}</p>
                            </div>
                          </Popup>
                        </>
                      )}
                    </CircleMarker>
                  )
                })}
              </LayerGroup>
            </LayersControl.Overlay>

            <LayersControl.Overlay name="Detected APs" checked={visibleLayers['Detected APs'] !== false}>
              <LayerGroup>
                {computedList.map((d, idx) => {
                  if (d.device_type !== 'AP') return null
                  const off = computedOffsets[idx] || [0, 0]
                  const style = confidenceStyle(d.confidence)
                  const name = d.ssids || d.known_label || ''
                  const isFocused = focusMac && d.mac === focusMac
                  return (
                    <CircleMarker key={`pos-${d.mac}`}
                      center={[parseFloat(d.lat) + off[0], parseFloat(d.lon) + off[1]]}
                      radius={isFocused ? 12 : zoom >= 20 ? 7 : 5}
                      pathOptions={isFocused
                        ? { fillColor: '#f59e0b', fillOpacity: 0.9, color: '#fbbf24', weight: 3, opacity: 1 }
                        : { fillColor: style.fillColor, fillOpacity: 0.7, color: style.color, weight: 2, opacity: 0.9 }}
                      interactive={!isPlacing}>
                      {!isPlacing && (
                        <>
                          <Tooltip direction="right" offset={[8, 0]}
                            permanent={zoom >= 21 && !!name}
                            className="device-tooltip">
                            {name || d.manufacturer || d.mac}
                          </Tooltip>
                          <Popup>
                            <div className="text-sm space-y-1">
                              <p className="font-bold">{d.ssids || 'Hidden'}</p>
                              <p className="text-xs font-mono">{d.mac}</p>
                              {d.known_label && <p className="text-xs">{d.known_label}</p>}
                              {d.manufacturer && <p className="text-xs">{d.manufacturer}</p>}
                              <p className="text-xs">Confidence: {parseFloat(d.confidence).toFixed(0)}% ({d.method}, {d.scanner_count} scanners)</p>
                              <p className="text-xs">Status: {d.status || 'unknown'}</p>
                            </div>
                          </Popup>
                        </>
                      )}
                    </CircleMarker>
                  )
                })}
              </LayerGroup>
            </LayersControl.Overlay>
          </LayersControl>

          {/* Tether lines from spread positions back to true position */}
          {computedList.map((d, idx) => {
            const off = computedOffsets[idx]
            if (!off || (off[0] === 0 && off[1] === 0)) return null
            const realLat = parseFloat(d.lat)
            const realLon = parseFloat(d.lon)
            return (
              <Polyline key={`tether-${d.mac}`}
                positions={[[realLat + off[0], realLon + off[1]], [realLat, realLon]]}
                pathOptions={{ color: '#6b7280', weight: 1, opacity: 0.5, dashArray: '4 4' }}
                interactive={false} />
            )
          })}
          {placedAPList.map((ap, idx) => {
            const off = apOffsets[idx]
            if (!off || (off[0] === 0 && off[1] === 0)) return null
            const realLat = parseFloat(ap.lat)
            const realLon = parseFloat(ap.lon)
            return (
              <Polyline key={`tether-ap-${ap.mac}`}
                positions={[[realLat + off[0], realLon + off[1]], [realLat, realLon]]}
                pathOptions={{ color: '#6b7280', weight: 1, opacity: 0.5, dashArray: '4 4' }}
                interactive={false} />
            )
          })}
          {fixedDeviceList.map((device, idx) => {
            const off = fixedOffsets[idx]
            if (!off || (off[0] === 0 && off[1] === 0)) return null
            const realLat = parseFloat(device.lat)
            const realLon = parseFloat(device.lon)
            return (
              <Polyline key={`tether-fixed-${device.mac}`}
                positions={[[realLat + off[0], realLon + off[1]], [realLat, realLon]]}
                pathOptions={{ color: '#6b7280', weight: 1, opacity: 0.5, dashArray: '4 4' }}
                interactive={false} />
            )
          })}

          <MapRecenter
            lat={mapConfig?.gps_anchor_lat ? parseFloat(mapConfig.gps_anchor_lat) : null}
            lon={mapConfig?.gps_anchor_lon ? parseFloat(mapConfig.gps_anchor_lon) : null}
            allPoints={allPoints}
            focusPoint={focusPoint} />

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

          {/* Floor pane: z-index below overlayPane so markers/circles take click priority */}
          <FloorPane />

          {/* Saved floor zones (render first so walls draw on top) */}
          <SavedFloors
            floors={floorList}
            selectedId={selectedItem?._type === 'floor' ? selectedItem.id : null}
            onSelect={(f) => handleSelectDrawn(f, 'floor')}
            pane="floorPane"
          />

          {/* Saved walls */}
          <SavedWalls
            walls={wallList}
            selectedId={selectedItem?._type === 'wall' ? selectedItem.id : null}
            onSelect={(w) => handleSelectDrawn(w, 'wall')}
          />


          {/* Highlight ring on searched/focused device */}
          {focusMac && focusPoint && (
            <Marker position={focusPoint} icon={highlightIcon} interactive={false} zIndexOffset={1000} />
          )}

          {/* Pending placement marker — draggable for fine-tuning */}
          {pendingPos && (
            <Marker position={[pendingPos.lat, pendingPos.lng]} icon={pendingIcon} draggable
              eventHandlers={{ dragend: (e) => { const ll = e.target.getLatLng(); setPendingPos({ lat: ll.lat, lng: ll.lng }) } }}>
              <Popup><p className="text-sm">Drag to fine-tune position</p></Popup>
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
        <div className="flex items-center gap-1"><span className="w-3 h-3 bg-cyan-500/30 rounded border border-cyan-300" /> Fixed device</div>
        <div className="flex items-center gap-1"><span className="w-2.5 h-2.5 bg-blue-500/60 rounded-full border border-blue-400" /> Computed (high)</div>
        <div className="flex items-center gap-1"><span className="w-2 h-2 bg-amber-500/60 rounded-full border border-amber-400" /> Computed (med)</div>
        <div className="flex items-center gap-1"><span className="w-2 h-2 bg-gray-500/60 rounded-full border border-gray-400" /> Computed (low)</div>
      </div>
    </div>
  )
}
