import { Routes, Route, Navigate } from 'react-router-dom'
import Layout from './components/Layout'
import Dashboard from './pages/Dashboard'
import Devices from './pages/Devices'
import DeviceDetail from './pages/DeviceDetail'
import MapView from './pages/MapView'
import Scanners from './pages/Scanners'
import MobileMap from './pages/MobileMap'
import Calibrate from './pages/Calibrate'

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<Navigate to="/dashboard" replace />} />
        <Route path="/dashboard" element={<Dashboard />} />
        <Route path="/devices" element={<Devices />} />
        <Route path="/devices/:mac" element={<DeviceDetail />} />
        <Route path="/map" element={<MapView />} />
        <Route path="/mobile" element={<MobileMap />} />
        <Route path="/calibrate" element={<Calibrate />} />
        <Route path="/scanners" element={<Scanners />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  )
}
