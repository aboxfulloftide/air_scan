import { useState } from 'react'
import { Square, MousePointer, X, Minus, Check } from 'lucide-react'
const WALL_TYPES = [
  { value: 'exterior', label: 'Exterior Wall', color: '#ef4444', desc: '~8 dB loss' },
  { value: 'interior', label: 'Interior Wall', color: '#f59e0b', desc: '~3 dB loss' },
  { value: 'addition', label: 'Addition Wall', color: '#f97316', desc: '~6 dB loss (exterior inside)' },
]

const FLOOR_TYPES = [
  { value: 'hardwood', label: 'Hardwood', color: '#92400e' },
  { value: 'tile', label: 'Tile', color: '#6b7280' },
  { value: 'carpet', label: 'Carpet', color: '#7c3aed' },
  { value: 'concrete', label: 'Concrete', color: '#4b5563' },
  { value: 'laminate', label: 'Laminate', color: '#b45309' },
  { value: 'vinyl', label: 'Vinyl', color: '#0891b2' },
]

// modes: 'select' | 'wall_line' | 'wall_freehand' | 'floor_zone'
export default function DrawingToolbar({ mode, onModeChange, subType, onSubTypeChange, pointCount = 0, onDone }) {
  const [expanded, setExpanded] = useState(null) // 'wall' | 'floor' | null

  const isDrawing = mode !== 'select'
  const minPoints = mode === 'floor_zone' ? 3 : 2
  const canFinish = pointCount >= minPoints

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-lg p-3 space-y-2">
      <div className="flex items-center gap-1">
        {/* Select / pointer mode */}
        <button
          onClick={() => { onModeChange('select'); setExpanded(null) }}
          className={`p-2 rounded ${mode === 'select' ? 'bg-blue-600 text-white' : 'bg-gray-800 text-gray-400 hover:text-white'}`}
          title="Select"
        >
          <MousePointer className="w-4 h-4" />
        </button>

        <div className="w-px h-6 bg-gray-700 mx-1" />

        {/* Wall drawing */}
        <button
          onClick={() => {
            setExpanded(expanded === 'wall' ? null : 'wall')
            if (expanded !== 'wall') {
              onModeChange('wall_line')
              onSubTypeChange('exterior')
            }
          }}
          className={`p-2 rounded flex items-center gap-1 ${
            mode.startsWith('wall_') ? 'bg-red-600 text-white' : 'bg-gray-800 text-gray-400 hover:text-white'
          }`}
          title="Draw Walls"
        >
          <Minus className="w-4 h-4" />
          <span className="text-xs">Walls</span>
        </button>

        {/* Floor zone drawing */}
        <button
          onClick={() => {
            setExpanded(expanded === 'floor' ? null : 'floor')
            if (expanded !== 'floor') {
              onModeChange('floor_zone')
              onSubTypeChange('hardwood')
            }
          }}
          className={`p-2 rounded flex items-center gap-1 ${
            mode === 'floor_zone' ? 'bg-purple-600 text-white' : 'bg-gray-800 text-gray-400 hover:text-white'
          }`}
          title="Draw Floor Zones"
        >
          <Square className="w-4 h-4" />
          <span className="text-xs">Floors</span>
        </button>

        {isDrawing && (
          <>
            <div className="w-px h-6 bg-gray-700 mx-1" />

            {/* Done button */}
            <button
              onClick={onDone}
              disabled={!canFinish}
              className={`p-2 rounded flex items-center gap-1 ${
                canFinish
                  ? 'bg-green-600 text-white hover:bg-green-500'
                  : 'bg-gray-800 text-gray-600 cursor-not-allowed'
              }`}
              title="Finish drawing (Enter)"
            >
              <Check className="w-4 h-4" />
              <span className="text-xs">Done</span>
            </button>

            <button
              onClick={() => { onModeChange('select'); setExpanded(null) }}
              className="p-2 rounded bg-gray-800 text-gray-400 hover:text-white"
              title="Cancel (Esc)"
            >
              <X className="w-4 h-4" />
            </button>
          </>
        )}
      </div>

      {/* Wall type selector + draw mode */}
      {expanded === 'wall' && (
        <div className="space-y-2 pt-1 border-t border-gray-800">
          <div className="flex gap-1">
            <button
              onClick={() => onModeChange('wall_line')}
              className={`px-3 py-1.5 rounded text-xs ${mode === 'wall_line' ? 'bg-gray-700 text-white' : 'bg-gray-800 text-gray-400'}`}
            >
              Straight Lines
            </button>
            <button
              onClick={() => onModeChange('wall_freehand')}
              className={`px-3 py-1.5 rounded text-xs ${mode === 'wall_freehand' ? 'bg-gray-700 text-white' : 'bg-gray-800 text-gray-400'}`}
            >
              Freehand
            </button>
          </div>
          <div className="flex flex-wrap gap-1">
            {WALL_TYPES.map((wt) => (
              <button
                key={wt.value}
                onClick={() => onSubTypeChange(wt.value)}
                className={`flex items-center gap-1.5 px-2 py-1 rounded text-xs border ${
                  subType === wt.value ? 'border-white text-white' : 'border-gray-700 text-gray-400'
                }`}
              >
                <span className="w-3 h-1 rounded" style={{ background: wt.color }} />
                {wt.label}
              </button>
            ))}
          </div>
          <p className="text-xs text-gray-500">
            {mode === 'wall_line'
              ? 'Click to add points. Press Done or Enter to finish.'
              : 'Click and drag to draw. Release to finish.'}
          </p>
        </div>
      )}

      {/* Floor type selector */}
      {expanded === 'floor' && (
        <div className="space-y-2 pt-1 border-t border-gray-800">
          <div className="flex flex-wrap gap-1">
            {FLOOR_TYPES.map((ft) => (
              <button
                key={ft.value}
                onClick={() => onSubTypeChange(ft.value)}
                className={`flex items-center gap-1.5 px-2 py-1 rounded text-xs border ${
                  subType === ft.value ? 'border-white text-white' : 'border-gray-700 text-gray-400'
                }`}
              >
                <span className="w-3 h-3 rounded" style={{ background: ft.color, opacity: 0.5 }} />
                {ft.label}
              </button>
            ))}
          </div>
          <p className="text-xs text-gray-500">Click to add corners. Press Done or Enter to close the shape.</p>
        </div>
      )}
    </div>
  )
}

export { WALL_TYPES, FLOOR_TYPES }
