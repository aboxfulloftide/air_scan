import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState, useRef, useEffect, useCallback } from 'react'
import api from '../api/client'
import { Plus, Pencil, Trash2, Check, X, Upload, Server, ChevronDown, ChevronUp, Play, Loader2 } from 'lucide-react'

const authLabels = { key: 'SSH Key', sshpass: 'Password (sshpass)' }
const svcLabels = { systemd: 'systemd', process: 'Process', none: 'None' }

const emptyTarget = {
  name: '', description: '', HOST: '', USER: 'matheau',
  REMOTE_PATH: '/home/matheau/code/air_scan/scanners/',
  SCP_FLAGS: '', AUTH: 'key', password: '',
  SERVICE_TYPE: 'systemd', SERVICE_NAME: '',
  START_CMD: '', STOP_CMD: '', PRE_START: '', POST_DEPLOY: '', FILES: [],
}

// Strip ANSI color codes for display
const stripAnsi = (s) => s.replace(/\x1b\[[0-9;]*m/g, '')

function DeployOutput({ output, running }) {
  const ref = useRef(null)
  useEffect(() => {
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight
  }, [output])

  if (!output && !running) return null

  return (
    <div className="bg-gray-950 border border-gray-800 rounded-lg overflow-hidden">
      <div className="flex items-center gap-2 px-3 py-2 bg-gray-900 border-b border-gray-800">
        {running && <Loader2 className="w-3.5 h-3.5 text-blue-400 animate-spin" />}
        <span className="text-xs text-gray-400">{running ? 'Deploying...' : 'Deploy output'}</span>
      </div>
      <pre ref={ref} className="p-3 text-xs text-gray-300 font-mono overflow-auto max-h-64 whitespace-pre-wrap">
        {output || 'Starting...'}
      </pre>
    </div>
  )
}

function useDeploy() {
  const [output, setOutput] = useState('')
  const [running, setRunning] = useState(false)

  const run = useCallback(async (url) => {
    setOutput('')
    setRunning(true)
    try {
      const res = await fetch(`/api/deploy/${url}`, { method: 'POST' })
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let text = ''
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        text += stripAnsi(decoder.decode(value, { stream: true }))
        setOutput(text)
      }
    } catch (err) {
      setOutput((prev) => prev + `\nError: ${err.message}`)
    } finally {
      setRunning(false)
    }
  }, [])

  const clear = useCallback(() => setOutput(''), [])

  return { output, running, run, clear }
}

function TargetForm({ initial, scannerFiles, onSave, onCancel, saving }) {
  const [form, setForm] = useState({ ...emptyTarget, ...initial })
  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }))

  const toggleFile = (name) => {
    set('FILES', form.FILES.includes(name)
      ? form.FILES.filter((f) => f !== name)
      : [...form.FILES, name])
  }

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <Field label="Name" value={form.name} onChange={(v) => set('name', v)}
          placeholder="e.g. office-pi5" disabled={!!initial?.name} />
        <Field label="Description" value={form.description} onChange={(v) => set('description', v)}
          placeholder="Short label for this target" />
        <Field label="Host" value={form.HOST} onChange={(v) => set('HOST', v)}
          placeholder="hostname or IP" />
        <Field label="User" value={form.USER} onChange={(v) => set('USER', v)} />
        <Field label="Remote Path" value={form.REMOTE_PATH} onChange={(v) => set('REMOTE_PATH', v)}
          placeholder="/home/user/air_scan/scanners/" />
        <SelectField label="Auth" value={form.AUTH} onChange={(v) => set('AUTH', v)}
          options={authLabels} />
        {form.AUTH === 'sshpass' && (
          <Field label="Password" value={form.password} onChange={(v) => set('password', v)}
            placeholder={initial?.has_password ? '(saved — leave blank to keep)' : 'SSH password'}
            type="password" />
        )}
        <Field label="SCP Flags" value={form.SCP_FLAGS} onChange={(v) => set('SCP_FLAGS', v)}
          placeholder="-O for OpenWrt (no SFTP)" />
        <SelectField label="Service Type" value={form.SERVICE_TYPE} onChange={(v) => set('SERVICE_TYPE', v)}
          options={svcLabels} />
        {form.SERVICE_TYPE !== 'none' && (
          <Field label="Service Name" value={form.SERVICE_NAME} onChange={(v) => set('SERVICE_NAME', v)}
            placeholder={form.SERVICE_TYPE === 'systemd' ? 'wifi-scanner.service' : 'router_capture'} />
        )}
      </div>

      {form.SERVICE_TYPE === 'process' && (
        <div className="space-y-3">
          <TextArea label="Start Command" value={form.START_CMD} onChange={(v) => set('START_CMD', v)}
            placeholder="sh /root/scanner.sh > /tmp/log 2>&1 &" />
          <TextArea label="Stop Command" value={form.STOP_CMD} onChange={(v) => set('STOP_CMD', v)}
            placeholder="kill $(ps | grep scanner | grep -v grep | awk '{print $1}')" />
          <TextArea label="Pre-Start" value={form.PRE_START} onChange={(v) => set('PRE_START', v)}
            placeholder="Commands to run before starting (e.g. interface setup)" />
        </div>
      )}

      <TextArea label="Post-Deploy Hook" value={form.POST_DEPLOY} onChange={(v) => set('POST_DEPLOY', v)}
        placeholder="Optional commands after file copy (e.g. chmod +x)" />

      <div>
        <label className="block text-sm text-gray-400 mb-2">Files to Deploy</label>
        <div className="space-y-1.5">
          {(scannerFiles || []).map((file) => {
            const name = typeof file === 'string' ? file : file.name
            const desc = typeof file === 'string' ? '' : file.desc
            const target = typeof file === 'string' ? '' : file.for
            const selected = form.FILES.includes(name)
            return (
              <button key={name} onClick={() => toggleFile(name)}
                className={`w-full text-left px-3 py-2 rounded border transition-colors flex items-center justify-between gap-3 ${
                  selected
                    ? 'bg-blue-600/20 border-blue-500 text-blue-300'
                    : 'bg-gray-800 border-gray-700 text-gray-400 hover:border-gray-600'
                }`}>
                <div className="min-w-0">
                  <span className="text-sm font-mono">{name}</span>
                  {desc && <p className="text-xs text-gray-500 truncate">{desc}</p>}
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  {target && (
                    <span className={`text-[10px] px-1.5 py-0.5 rounded ${
                      target === 'openwrt'
                        ? 'bg-orange-500/20 text-orange-400'
                        : 'bg-green-500/20 text-green-400'
                    }`}>{target}</span>
                  )}
                  {selected && <Check className="w-4 h-4 text-blue-400" />}
                </div>
              </button>
            )
          })}
        </div>
      </div>

      <div className="flex gap-2 pt-2">
        <button onClick={() => onSave(form)} disabled={saving || !form.name || !form.HOST}
          className="px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-40 rounded text-sm text-white flex items-center gap-2">
          <Check className="w-4 h-4" /> {saving ? 'Saving...' : 'Save'}
        </button>
        <button onClick={onCancel}
          className="px-4 py-2 bg-gray-800 hover:bg-gray-700 rounded text-sm text-gray-300">
          Cancel
        </button>
      </div>
    </div>
  )
}

function Field({ label, value, onChange, placeholder, disabled, hint, type = 'text' }) {
  return (
    <div>
      <label className="block text-xs text-gray-500 mb-1">{label}</label>
      <input type={type} value={value} onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder} disabled={disabled}
        className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-sm text-white
          placeholder:text-gray-600 focus:outline-none focus:border-blue-500 disabled:opacity-50" />
      {hint && <p className="text-[11px] text-gray-600 mt-0.5">{hint}</p>}
    </div>
  )
}

function SelectField({ label, value, onChange, options }) {
  return (
    <div>
      <label className="block text-xs text-gray-500 mb-1">{label}</label>
      <select value={value} onChange={(e) => onChange(e.target.value)}
        className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-sm text-white
          focus:outline-none focus:border-blue-500">
        {Object.entries(options).map(([k, v]) => (
          <option key={k} value={k}>{v}</option>
        ))}
      </select>
    </div>
  )
}

function TextArea({ label, value, onChange, placeholder }) {
  return (
    <div>
      <label className="block text-xs text-gray-500 mb-1">{label}</label>
      <textarea value={value} onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder} rows={2}
        className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-sm text-white
          font-mono placeholder:text-gray-600 focus:outline-none focus:border-blue-500" />
    </div>
  )
}

function TargetCard({ target, scannerFiles, onDelete, onDeploy, deploying }) {
  const queryClient = useQueryClient()
  const [editing, setEditing] = useState(false)
  const [expanded, setExpanded] = useState(false)

  const updateTarget = useMutation({
    mutationFn: (data) => api.put(`/deploy/targets/${target.name}`, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['deploy-targets'] })
      setEditing(false)
    },
  })

  if (editing) {
    return (
      <div className="bg-gray-900 border border-gray-800 rounded-lg p-5">
        <h4 className="text-white font-medium mb-4">Edit: {target.name}</h4>
        <TargetForm initial={target} scannerFiles={scannerFiles}
          onSave={(data) => updateTarget.mutate(data)}
          onCancel={() => setEditing(false)}
          saving={updateTarget.isPending} />
      </div>
    )
  }

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-lg p-5 space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Server className="w-4 h-4 text-gray-500" />
          <div>
            <h4 className="font-medium text-white">{target.name}</h4>
            {target.description && (
              <p className="text-xs text-gray-500">{target.description}</p>
            )}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={() => onDeploy(target.name)} disabled={deploying}
            className="px-2.5 py-1 bg-green-600 hover:bg-green-500 disabled:opacity-40 rounded text-xs text-white flex items-center gap-1.5"
            title="Deploy to this target">
            {deploying
              ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
              : <Play className="w-3.5 h-3.5" />}
            Deploy
          </button>
          <button onClick={() => setEditing(true)} className="text-gray-500 hover:text-white">
            <Pencil className="w-3.5 h-3.5" />
          </button>
          <button onClick={() => onDelete(target.name)} className="text-gray-500 hover:text-red-400">
            <Trash2 className="w-3.5 h-3.5" />
          </button>
          <button onClick={() => setExpanded(!expanded)} className="text-gray-500 hover:text-white ml-1">
            {expanded ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
          </button>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-y-1 text-sm">
        <span className="text-gray-500">Host</span>
        <span className="text-gray-300 font-mono text-xs">{target.USER}@{target.HOST}</span>
        <span className="text-gray-500">Auth</span>
        <span className="text-gray-300 flex items-center gap-1.5">
          {authLabels[target.AUTH] || target.AUTH}
          {target.AUTH === 'sshpass' && (
            <span className={`text-[10px] px-1.5 py-0.5 rounded ${target.has_password ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-400'}`}>
              {target.has_password ? 'password saved' : 'no password'}
            </span>
          )}
        </span>
        <span className="text-gray-500">Service</span>
        <span className="text-gray-300">
          {target.SERVICE_TYPE !== 'none' && target.SERVICE_NAME
            ? `${target.SERVICE_NAME} (${target.SERVICE_TYPE})`
            : <span className="text-gray-600">none</span>}
        </span>
        <span className="text-gray-500">Files</span>
        <span className="text-gray-300 text-xs">
          {(target.FILES || []).length > 0
            ? target.FILES.join(', ')
            : <span className="text-gray-600">none selected</span>}
        </span>
      </div>

      {expanded && (
        <div className="grid grid-cols-2 gap-y-1 text-sm border-t border-gray-800 pt-3">
          <span className="text-gray-500">Remote Path</span>
          <span className="text-gray-300 font-mono text-xs">{target.REMOTE_PATH}</span>
          {target.SCP_FLAGS && <>
            <span className="text-gray-500">SCP Flags</span>
            <span className="text-gray-300 font-mono text-xs">{target.SCP_FLAGS}</span>
          </>}
          {target.START_CMD && <>
            <span className="text-gray-500">Start Cmd</span>
            <span className="text-gray-300 font-mono text-xs truncate">{target.START_CMD}</span>
          </>}
          {target.STOP_CMD && <>
            <span className="text-gray-500">Stop Cmd</span>
            <span className="text-gray-300 font-mono text-xs truncate">{target.STOP_CMD.split('\n')[0]}...</span>
          </>}
          {target.POST_DEPLOY && <>
            <span className="text-gray-500">Post-Deploy</span>
            <span className="text-gray-300 font-mono text-xs truncate">{target.POST_DEPLOY}</span>
          </>}
        </div>
      )}
    </div>
  )
}

export default function DeployTargets() {
  const queryClient = useQueryClient()
  const [creating, setCreating] = useState(false)
  const deploy = useDeploy()

  const { data: targets, isLoading } = useQuery({
    queryKey: ['deploy-targets'],
    queryFn: () => api.get('/deploy/targets').then((r) => r.data),
  })

  const { data: scannerFiles } = useQuery({
    queryKey: ['deploy-files'],
    queryFn: () => api.get('/deploy/files').then((r) => r.data),
  })

  const createTarget = useMutation({
    mutationFn: (data) => api.post('/deploy/targets', data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['deploy-targets'] })
      setCreating(false)
    },
  })

  const deleteTarget = useMutation({
    mutationFn: (name) => api.delete(`/deploy/targets/${name}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['deploy-targets'] }),
  })

  const handleDelete = (name) => {
    if (window.confirm(`Delete deploy target "${name}"?`)) {
      deleteTarget.mutate(name)
    }
  }

  if (isLoading) return <div className="text-gray-500 text-sm">Loading deploy targets...</div>

  const hasTargets = targets && targets.length > 0

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-lg font-semibold text-white flex items-center gap-2">
          <Upload className="w-5 h-5 text-gray-400" /> Deploy Targets
        </h3>
        <div className="flex items-center gap-2">
          {hasTargets && targets.length > 1 && (
            <button onClick={() => deploy.run('run-all')} disabled={deploy.running}
              className="px-3 py-1.5 bg-green-600 hover:bg-green-500 disabled:opacity-40 rounded text-sm text-white flex items-center gap-1.5">
              {deploy.running
                ? <Loader2 className="w-4 h-4 animate-spin" />
                : <Play className="w-4 h-4" />}
              Deploy All
            </button>
          )}
          {!creating && (
            <button onClick={() => setCreating(true)}
              className="px-3 py-1.5 bg-blue-600 hover:bg-blue-500 rounded text-sm text-white flex items-center gap-1.5">
              <Plus className="w-4 h-4" /> Add Target
            </button>
          )}
        </div>
      </div>

      {creating && (
        <div className="bg-gray-900 border border-blue-500/30 rounded-lg p-5">
          <h4 className="text-white font-medium mb-4">New Deploy Target</h4>
          <TargetForm scannerFiles={scannerFiles}
            onSave={(data) => createTarget.mutate(data)}
            onCancel={() => setCreating(false)}
            saving={createTarget.isPending} />
          {createTarget.isError && (
            <p className="text-red-400 text-sm mt-2">
              {createTarget.error?.response?.data?.detail || 'Failed to create target'}
            </p>
          )}
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {(targets || []).map((t) => (
          <TargetCard key={t.name} target={t} scannerFiles={scannerFiles}
            onDelete={handleDelete}
            onDeploy={(name) => deploy.run(`run/${name}`)}
            deploying={deploy.running} />
        ))}
      </div>

      <DeployOutput output={deploy.output} running={deploy.running} />

      {!creating && !hasTargets && (
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-6 text-center">
          <Server className="w-10 h-10 text-gray-600 mx-auto mb-2" />
          <p className="text-gray-400 text-sm">No deploy targets configured.</p>
          <p className="text-gray-600 text-xs mt-1">Add a target to push scanner updates to remote hosts.</p>
        </div>
      )}
    </div>
  )
}
