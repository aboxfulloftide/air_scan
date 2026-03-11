import { useState, useRef, useEffect, useCallback } from 'react'
import { useMapEvents, Polyline, Polygon, CircleMarker, useMap } from 'react-leaflet'

const WALL_COLORS = { exterior: '#ef4444', interior: '#f59e0b', addition: '#f97316' }
const FLOOR_COLORS = {
  hardwood: '#92400e', tile: '#6b7280', carpet: '#7c3aed',
  concrete: '#4b5563', laminate: '#b45309', vinyl: '#0891b2',
}

// Simplify a freehand path by removing points too close together
function simplifyPath(points, minDist = 0.000005) {
  if (points.length < 2) return points
  const result = [points[0]]
  for (let i = 1; i < points.length; i++) {
    const prev = result[result.length - 1]
    const dx = points[i][0] - prev[0]
    const dy = points[i][1] - prev[1]
    if (Math.sqrt(dx * dx + dy * dy) >= minDist) {
      result.push(points[i])
    }
  }
  // Always include last point
  if (result.length > 1 && result[result.length - 1] !== points[points.length - 1]) {
    result.push(points[points.length - 1])
  }
  return result
}

// Drawing event handler
function DrawingEvents({ drawMode, subType, onFinishWall, onFinishFloor, onPointCountChange, finishRef }) {
  const [points, setPoints] = useState([])
  const [mousePos, setMousePos] = useState(null)
  const [isDrawingFreehand, setIsDrawingFreehand] = useState(false)
  const freehandRef = useRef([])
  const pointsRef = useRef([])
  const map = useMap()

  // Keep ref in sync with state
  useEffect(() => { pointsRef.current = points }, [points])

  // Reset points when mode changes
  useEffect(() => {
    setPoints([])
    setMousePos(null)
    pointsRef.current = []
    freehandRef.current = []
  }, [drawMode, subType])

  // Keyboard: Enter/Escape to finish
  useEffect(() => {
    const handler = (e) => {
      if (e.key === 'Enter' || e.key === 'Escape') {
        finishDrawing()
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  })

  const finishDrawing = useCallback(() => {
    const pts = pointsRef.current
    if (drawMode === 'wall_line' && pts.length >= 2) {
      onFinishWall([...pts])
    } else if (drawMode === 'floor_zone' && pts.length >= 3) {
      onFinishFloor([...pts])
    }
    setPoints([])
    setMousePos(null)
    pointsRef.current = []
  }, [drawMode, onFinishWall, onFinishFloor])

  // Expose finish function and point count to parent
  useEffect(() => {
    if (finishRef) finishRef.current = finishDrawing
  }, [finishDrawing, finishRef])

  useEffect(() => {
    if (onPointCountChange) onPointCountChange(points.length)
  }, [points.length, onPointCountChange])

  // Disable map dragging during freehand
  useEffect(() => {
    if (drawMode === 'wall_freehand') {
      map.dragging.disable()
      return () => map.dragging.enable()
    }
  }, [drawMode, map])

  const handleMouseDown = useCallback((e) => {
    if (drawMode === 'wall_freehand') {
      setIsDrawingFreehand(true)
      freehandRef.current = [[e.latlng.lat, e.latlng.lng]]
      setPoints([[e.latlng.lat, e.latlng.lng]])
    }
  }, [drawMode])

  const handleMouseMove = useCallback((e) => {
    const pt = [e.latlng.lat, e.latlng.lng]

    // Freehand: accumulate points while dragging
    if (drawMode === 'wall_freehand' && isDrawingFreehand) {
      freehandRef.current.push(pt)
      setPoints([...freehandRef.current])
      return
    }

    // Straight lines / floor zones: track mouse for preview
    if (drawMode === 'wall_line' || drawMode === 'floor_zone') {
      setMousePos(pt)
    }
  }, [drawMode, isDrawingFreehand])

  const handleMouseUp = useCallback(() => {
    if (drawMode === 'wall_freehand' && isDrawingFreehand) {
      setIsDrawingFreehand(false)
      const simplified = simplifyPath(freehandRef.current)
      if (simplified.length >= 2) {
        onFinishWall(simplified)
      }
      freehandRef.current = []
      setPoints([])
      pointsRef.current = []
    }
  }, [drawMode, isDrawingFreehand, onFinishWall])

  useMapEvents({
    click(e) {
      if (drawMode === 'wall_line' || drawMode === 'floor_zone') {
        const pt = [e.latlng.lat, e.latlng.lng]
        setPoints((prev) => {
          const next = [...prev, pt]
          pointsRef.current = next
          return next
        })
      }
    },
    mousedown: handleMouseDown,
    mousemove: handleMouseMove,
    mouseup: handleMouseUp,
  })

  const wallColor = WALL_COLORS[subType] || '#f59e0b'
  const floorColor = FLOOR_COLORS[subType] || '#6b7280'

  // Build the preview path: placed points + mouse position
  const previewPoints = mousePos && points.length > 0 ? [...points, mousePos] : points

  return (
    <>
      {/* Wall drawing: placed segments + preview to cursor */}
      {(drawMode === 'wall_line' || drawMode === 'wall_freehand') && previewPoints.length >= 2 && (
        <>
          {/* Solid line for confirmed segments */}
          {points.length >= 2 && (
            <Polyline positions={points}
              pathOptions={{ color: wallColor, weight: 3, opacity: 0.9 }} />
          )}
          {/* Dashed preview line from last point to cursor */}
          {mousePos && points.length >= 1 && drawMode === 'wall_line' && (
            <Polyline positions={[points[points.length - 1], mousePos]}
              pathOptions={{ color: wallColor, weight: 2, dashArray: '6 4', opacity: 0.6 }} />
          )}
          {/* Freehand trail */}
          {drawMode === 'wall_freehand' && points.length >= 2 && (
            <Polyline positions={points}
              pathOptions={{ color: wallColor, weight: 3, dashArray: '6 4', opacity: 0.8 }} />
          )}
        </>
      )}

      {/* Floor zone drawing: polygon preview */}
      {drawMode === 'floor_zone' && previewPoints.length >= 2 && (
        <>
          {/* Outline as polyline so you see each segment */}
          <Polyline positions={previewPoints}
            pathOptions={{ color: floorColor, weight: 2, dashArray: '6 4', opacity: 0.7 }} />
          {/* Filled preview when 3+ points */}
          {previewPoints.length >= 3 && (
            <Polygon positions={previewPoints}
              pathOptions={{ color: floorColor, fillColor: floorColor, fillOpacity: 0.15, weight: 0 }} />
          )}
          {/* Dashed closing line from last point back to first */}
          {mousePos && points.length >= 2 && (
            <Polyline positions={[mousePos, points[0]]}
              pathOptions={{ color: floorColor, weight: 1, dashArray: '4 4', opacity: 0.4 }} />
          )}
        </>
      )}

      {/* Vertex dots at each clicked point */}
      {(drawMode === 'wall_line' || drawMode === 'floor_zone') && points.map((pt, i) => (
        <CircleMarker key={i} center={pt} radius={4}
          pathOptions={{
            color: drawMode === 'wall_line' ? wallColor : floorColor,
            fillColor: '#ffffff',
            fillOpacity: 1,
            weight: 2,
          }} />
      ))}

      {/* Single point indicator when only 1 point placed for walls */}
      {drawMode === 'wall_line' && points.length === 1 && mousePos && (
        <Polyline positions={[points[0], mousePos]}
          pathOptions={{ color: wallColor, weight: 2, dashArray: '6 4', opacity: 0.6 }} />
      )}
    </>
  )
}

// Render saved walls
function SavedWalls({ walls, selectedId, onSelect }) {
  return walls.map((w) => {
    let pts
    try {
      pts = typeof w.points_json === 'string' ? JSON.parse(w.points_json) : w.points_json
    } catch {
      return null
    }
    if (!pts || pts.length < 2) return null

    return (
      <Polyline
        key={`wall-${w.id}`}
        positions={pts}
        pathOptions={{
          color: WALL_COLORS[w.wall_type] || '#f59e0b',
          weight: selectedId === w.id ? 5 : 3,
          opacity: selectedId === w.id ? 1 : 0.7,
        }}
        eventHandlers={{ click: () => onSelect(w) }}
      />
    )
  })
}

// Render saved floor zones
function SavedFloors({ floors, selectedId, onSelect }) {
  return floors.map((f) => {
    let pts
    try {
      pts = typeof f.polygon_json === 'string' ? JSON.parse(f.polygon_json) : f.polygon_json
    } catch {
      return null
    }
    if (!pts || pts.length < 3) return null

    return (
      <Polygon
        key={`floor-${f.id}`}
        positions={pts}
        pathOptions={{
          color: FLOOR_COLORS[f.floor_type] || '#6b7280',
          fillColor: FLOOR_COLORS[f.floor_type] || '#6b7280',
          fillOpacity: selectedId === f.id ? 0.4 : 0.2,
          weight: selectedId === f.id ? 3 : 1,
        }}
        eventHandlers={{ click: () => onSelect(f) }}
      />
    )
  })
}

export { DrawingEvents, SavedWalls, SavedFloors, WALL_COLORS, FLOOR_COLORS }
