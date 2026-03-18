#!/usr/bin/env python3
"""
Mobile scanner status server.
Serves a phone-friendly page showing live GPS position and recent WiFi readings.
Reads directly from the mobile_scan SQLite DB — no scanner process required.

Usage:
    python3 mobile_status.py [--db PATH] [--port PORT] [--host HOST]

Defaults:
    - DB:   auto-detected (same logic as mobile_scanner.py)
    - Port: 8080
    - Host: 0.0.0.0 (reachable from any interface, including hotspot)
"""

import argparse
import json
import os
import sqlite3
import subprocess
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

# ---------------------------------------------------------------------------
# Args
# ---------------------------------------------------------------------------

parser = argparse.ArgumentParser(description="Mobile scanner status web server")
parser.add_argument("--db",   default=None, help="Path to mobile_scan.db")
parser.add_argument("--port", type=int, default=8080)
parser.add_argument("--host", default="0.0.0.0")
args = parser.parse_args()


# ---------------------------------------------------------------------------
# DB path — mirror auto-detection from mobile_scanner.py
# ---------------------------------------------------------------------------

def find_usb_storage():
    root_dev = None
    try:
        r = subprocess.run(["findmnt", "-n", "-o", "SOURCE", "/"],
                           capture_output=True, text=True)
        root_dev = r.stdout.strip()
    except Exception:
        pass
    for base in [Path("/media"), Path("/mnt")]:
        if not base.exists():
            continue
        for candidate in sorted(base.rglob("*")):
            if not candidate.is_dir() or candidate == base:
                continue
            try:
                r = subprocess.run(["findmnt", "-n", "-o", "SOURCE", str(candidate)],
                                   capture_output=True, text=True)
                src = r.stdout.strip()
                if not src or src == root_dev:
                    continue
            except Exception:
                pass
            try:
                st = os.statvfs(candidate)
                free_mb = (st.f_frsize * st.f_bavail) / (1024 ** 2)
                if free_mb > 50 and os.access(candidate, os.W_OK):
                    return candidate
            except Exception:
                continue
    return None


if args.db:
    DB_PATH = Path(args.db)
else:
    usb = find_usb_storage()
    if usb:
        DB_PATH = usb / "air_scan" / "mobile_scan.db"
    else:
        DB_PATH = Path("/tmp/air_scan/mobile_scan.db")

print(f"[STATUS] Using DB: {DB_PATH}")
print(f"[STATUS] Listening on http://{args.host}:{args.port}")


# ---------------------------------------------------------------------------
# Data queries
# ---------------------------------------------------------------------------

def query_status():
    """Return dict with gps and wifi data, or error."""
    if not DB_PATH.exists():
        return {"error": f"DB not found: {DB_PATH}", "gps": None, "wifi": []}

    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True,
                               check_same_thread=False)
        conn.row_factory = sqlite3.Row

        # Most recent GPS fix from the last 5 minutes
        gps_row = conn.execute("""
            SELECT gps_lat, gps_lon, gps_fix, recorded_at
            FROM observations
            WHERE gps_lat IS NOT NULL
              AND recorded_at >= datetime('now', '-5 minutes')
            ORDER BY recorded_at DESC
            LIMIT 1
        """).fetchone()

        gps = None
        if gps_row:
            gps = {
                "lat":        gps_row["gps_lat"],
                "lon":        gps_row["gps_lon"],
                "fix":        bool(gps_row["gps_fix"]),
                "recorded_at": gps_row["recorded_at"],
            }

        # Devices seen in last 60 seconds, best RSSI per MAC
        wifi_rows = conn.execute("""
            SELECT
                o.mac,
                MAX(o.signal_dbm)  AS signal_dbm,
                o.channel,
                o.freq_mhz,
                d.device_type,
                d.manufacturer,
                d.is_randomized,
                GROUP_CONCAT(DISTINCT s.ssid) AS ssids
            FROM observations o
            LEFT JOIN devices d ON d.mac = o.mac
            LEFT JOIN ssids   s ON s.mac = o.mac
            WHERE o.recorded_at >= datetime('now', '-60 seconds')
            GROUP BY o.mac
            ORDER BY signal_dbm DESC
            LIMIT 60
        """).fetchall()

        wifi = []
        for row in wifi_rows:
            wifi.append({
                "mac":         row["mac"],
                "signal_dbm":  row["signal_dbm"],
                "channel":     row["channel"],
                "freq_mhz":    row["freq_mhz"],
                "device_type": row["device_type"] or "?",
                "manufacturer": row["manufacturer"] or "",
                "is_randomized": bool(row["is_randomized"]),
                "ssids":       row["ssids"] or "",
            })

        # Session summary
        session_row = conn.execute("""
            SELECT started_at, COUNT(*) AS total_obs
            FROM sessions s
            JOIN observations o ON o.session_id = s.id
            WHERE s.ended_at IS NULL
            ORDER BY s.id DESC
            LIMIT 1
        """).fetchone()

        session = None
        if session_row:
            session = {
                "started_at": session_row["started_at"],
                "total_obs":  session_row["total_obs"],
            }

        conn.close()
        return {"gps": gps, "wifi": wifi, "session": session, "error": None}

    except Exception as e:
        return {"error": str(e), "gps": None, "wifi": [], "session": None}


# ---------------------------------------------------------------------------
# HTML page (served once; JS polls /api/status)
# ---------------------------------------------------------------------------

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Air Scan — Mobile Status</title>
<style>
  :root {
    --bg:     #0f1117;
    --card:   #1a1d27;
    --border: #2a2d3a;
    --text:   #e2e6f0;
    --muted:  #8b92a5;
    --green:  #3ecf8e;
    --red:    #f66;
    --yellow: #f5a623;
    --blue:   #4b9ef5;
    --mono:   'SF Mono', 'Fira Code', monospace;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    font-size: 14px;
    padding: 12px;
    max-width: 640px;
    margin: 0 auto;
  }
  h1 { font-size: 18px; font-weight: 600; margin-bottom: 2px; }
  .subtitle { color: var(--muted); font-size: 12px; margin-bottom: 14px; }
  .card {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 14px 16px;
    margin-bottom: 12px;
  }
  .card-title {
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--muted);
    margin-bottom: 10px;
  }
  .badge {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 20px;
    font-size: 11px;
    font-weight: 600;
    margin-left: 6px;
  }
  .badge-green { background: rgba(62,207,142,0.15); color: var(--green); }
  .badge-red   { background: rgba(255,102,102,0.15); color: var(--red); }
  .badge-yellow{ background: rgba(245,166,35,0.15);  color: var(--yellow); }
  .gps-coords {
    font-family: var(--mono);
    font-size: 22px;
    font-weight: 600;
    letter-spacing: 0.03em;
    margin: 8px 0;
    line-height: 1.4;
  }
  .gps-meta { color: var(--muted); font-size: 12px; margin-top: 4px; }
  .map-btn {
    display: inline-block;
    margin-top: 10px;
    padding: 7px 14px;
    background: var(--blue);
    color: #fff;
    border-radius: 6px;
    text-decoration: none;
    font-size: 13px;
    font-weight: 500;
  }
  .map-btn:hover { opacity: 0.85; }
  .no-fix { color: var(--muted); font-style: italic; font-size: 14px; padding: 6px 0; }
  table { width: 100%; border-collapse: collapse; font-size: 12px; }
  th {
    text-align: left;
    color: var(--muted);
    font-weight: 600;
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    padding: 0 4px 8px 0;
    border-bottom: 1px solid var(--border);
  }
  td {
    padding: 7px 4px 7px 0;
    border-bottom: 1px solid var(--border);
    vertical-align: top;
  }
  tr:last-child td { border-bottom: none; }
  .mac   { font-family: var(--mono); color: var(--muted); font-size: 11px; }
  .ssid  { font-weight: 500; color: var(--text); }
  .rssi  { font-family: var(--mono); font-weight: 600; white-space: nowrap; }
  .rssi-strong { color: var(--green); }
  .rssi-medium { color: var(--yellow); }
  .rssi-weak   { color: var(--red); }
  .type-ap     { color: var(--blue); }
  .type-client { color: var(--muted); }
  .mfr { color: var(--muted); font-size: 11px; margin-top: 1px; }
  .rand-badge {
    display: inline-block;
    font-size: 9px;
    padding: 1px 4px;
    border-radius: 3px;
    background: rgba(245,166,35,0.12);
    color: var(--yellow);
    margin-left: 4px;
    vertical-align: middle;
  }
  .status-bar {
    display: flex;
    justify-content: space-between;
    align-items: center;
    color: var(--muted);
    font-size: 11px;
    margin-bottom: 12px;
  }
  .dot {
    display: inline-block;
    width: 6px; height: 6px;
    border-radius: 50%;
    background: var(--green);
    margin-right: 5px;
    animation: pulse 2s infinite;
  }
  .dot-stale { background: var(--red); animation: none; }
  @keyframes pulse {
    0%, 100% { opacity: 1; }
    50%       { opacity: 0.4; }
  }
  .error-msg {
    color: var(--red);
    background: rgba(255,102,102,0.08);
    border: 1px solid rgba(255,102,102,0.2);
    border-radius: 8px;
    padding: 10px 14px;
    font-family: var(--mono);
    font-size: 12px;
  }
  .empty { color: var(--muted); font-style: italic; padding: 8px 0; }
  .power-row {
    display: flex;
    gap: 10px;
    margin-bottom: 12px;
  }
  .power-btn {
    flex: 1;
    padding: 11px 0;
    border: none;
    border-radius: 8px;
    font-size: 14px;
    font-weight: 600;
    cursor: pointer;
    transition: opacity 0.15s;
  }
  .power-btn:active { opacity: 0.7; }
  .btn-reboot   { background: rgba(75,158,245,0.15); color: var(--blue);   border: 1px solid rgba(75,158,245,0.3); }
  .btn-shutdown { background: rgba(255,102,102,0.12); color: var(--red);   border: 1px solid rgba(255,102,102,0.3); }
  .power-btn:disabled { opacity: 0.4; cursor: not-allowed; }
</style>
</head>
<body>

<h1>Air Scan</h1>
<p class="subtitle">Mobile Scanner Status</p>

<div class="status-bar">
  <span><span class="dot" id="dot"></span><span id="status-text">Connecting…</span></span>
  <span id="last-update"></span>
</div>

<div id="error-box" style="display:none" class="error-msg"></div>

<div class="power-row">
  <button class="power-btn btn-reboot"   id="btn-reboot"   onclick="powerAction('reboot')">&#8635; Reboot</button>
  <button class="power-btn btn-shutdown" id="btn-shutdown" onclick="powerAction('shutdown')">&#9210; Shut Down</button>
</div>

<div class="card" id="gps-card">
  <div class="card-title">GPS <span id="fix-badge"></span></div>
  <div id="gps-body"><div class="no-fix">Waiting for data…</div></div>
</div>

<div class="card">
  <div class="card-title">WiFi — Last 60 s <span id="wifi-count"></span></div>
  <div id="wifi-body"><div class="empty">Waiting for data…</div></div>
</div>

<script>
const POLL_MS = 3000;

function rssiClass(v) {
  if (v === null || v === undefined) return '';
  if (v >= -65) return 'rssi-strong';
  if (v >= -80) return 'rssi-medium';
  return 'rssi-weak';
}

function rssiLabel(v) {
  return (v !== null && v !== undefined) ? v + ' dBm' : '—';
}

function chanLabel(row) {
  if (!row.channel && !row.freq_mhz) return '—';
  if (row.freq_mhz >= 5945) return 'ch' + row.channel + ' (6G)';
  if (row.freq_mhz >= 5000) return 'ch' + row.channel + ' (5G)';
  return 'ch' + (row.channel || '?');
}

function typeClass(t) {
  return t === 'AP' ? 'type-ap' : 'type-client';
}

function escHtml(s) {
  return String(s)
    .replace(/&/g,'&amp;')
    .replace(/</g,'&lt;')
    .replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;');
}

function mapsUrl(lat, lon) {
  return 'https://www.google.com/maps?q=' + lat + ',' + lon;
}

function render(data) {
  // Error
  const errBox = document.getElementById('error-box');
  if (data.error) {
    errBox.style.display = '';
    errBox.textContent = data.error;
  } else {
    errBox.style.display = 'none';
  }

  // GPS
  const gpsBody = document.getElementById('gps-body');
  const fixBadge = document.getElementById('fix-badge');
  const g = data.gps;
  if (!g) {
    gpsBody.innerHTML = '<div class="no-fix">No GPS data yet — is the scanner running?</div>';
    fixBadge.innerHTML = '<span class="badge badge-red">NO FIX</span>';
  } else {
    const fixHtml = g.fix
      ? '<span class="badge badge-green">FIX</span>'
      : '<span class="badge badge-yellow">STALE</span>';
    fixBadge.innerHTML = fixHtml;
    const mapsLink = g.lat
      ? '<a class="map-btn" href="' + mapsUrl(g.lat, g.lon) + '" target="_blank">Open in Maps</a>'
      : '';
    gpsBody.innerHTML =
      '<div class="gps-coords">' +
        escHtml(g.lat.toFixed(6)) + '<br>' + escHtml(g.lon.toFixed(6)) +
      '</div>' +
      '<div class="gps-meta">Updated ' + escHtml(g.recorded_at) + ' UTC</div>' +
      mapsLink;
  }

  // WiFi
  const wifiBody = document.getElementById('wifi-body');
  const wifiCount = document.getElementById('wifi-count');
  const wf = data.wifi || [];
  wifiCount.textContent = wf.length ? '(' + wf.length + ')' : '';
  if (!wf.length) {
    wifiBody.innerHTML = '<div class="empty">No devices in last 60 s</div>';
  } else {
    let rows = wf.map(row => {
      const randBadge = row.is_randomized ? '<span class="rand-badge">rand</span>' : '';
      const ssidPart  = row.ssids
        ? '<div class="ssid">' + escHtml(row.ssids.split(',').map(s=>s.trim()).filter(Boolean).join(', ')) + '</div>'
        : '';
      const mfrPart   = row.manufacturer
        ? '<div class="mfr">' + escHtml(row.manufacturer) + '</div>'
        : '';
      return '<tr>' +
        '<td>' +
          ssidPart +
          '<div class="mac">' + escHtml(row.mac) + randBadge + '</div>' +
          mfrPart +
        '</td>' +
        '<td class="' + typeClass(row.device_type) + '">' + escHtml(row.device_type) + '</td>' +
        '<td><span class="rssi ' + rssiClass(row.signal_dbm) + '">' + rssiLabel(row.signal_dbm) + '</span></td>' +
        '<td>' + chanLabel(row) + '</td>' +
        '</tr>';
    }).join('');
    wifiBody.innerHTML =
      '<table>' +
        '<thead><tr><th>Device</th><th>Type</th><th>RSSI</th><th>Ch</th></tr></thead>' +
        '<tbody>' + rows + '</tbody>' +
      '</table>';
  }

  // Status bar
  const dot  = document.getElementById('dot');
  const stxt = document.getElementById('status-text');
  const upd  = document.getElementById('last-update');
  const now  = new Date();
  dot.className = 'dot';
  stxt.textContent = data.error ? 'DB error' : 'Live';
  upd.textContent = now.toLocaleTimeString();
}

async function poll() {
  try {
    const resp = await fetch('/api/status');
    const data = await resp.json();
    render(data);
  } catch (e) {
    const dot = document.getElementById('dot');
    dot.className = 'dot dot-stale';
    document.getElementById('status-text').textContent = 'Offline';
  }
}

poll();
setInterval(poll, POLL_MS);

async function powerAction(action) {
  const label = action === 'reboot' ? 'Reboot' : 'Shut Down';
  if (!confirm(label + ' the Pi?')) return;
  const btn = document.getElementById('btn-' + action);
  btn.disabled = true;
  btn.textContent = action === 'reboot' ? 'Rebooting…' : 'Shutting down…';
  try {
    const resp = await fetch('/api/' + action, { method: 'POST' });
    const data = await resp.json();
    if (data.ok) {
      document.getElementById('status-text').textContent =
        action === 'reboot' ? 'Rebooting — reconnect in ~30s' : 'Shut down';
      document.getElementById('dot').className = 'dot dot-stale';
    } else {
      alert('Error: ' + (data.error || 'unknown'));
      btn.disabled = false;
      btn.textContent = action === 'reboot' ? '↻ Reboot' : '⏻ Shut Down';
    }
  } catch (e) {
    alert('Request failed: ' + e);
    btn.disabled = false;
  }
}
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class StatusHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *a):
        pass  # suppress default access log noise

    def send_json(self, data, code=200):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, html):
        body = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            self.send_html(HTML)
        elif path == "/api/status":
            self.send_json(query_status())
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        path = self.path.split("?")[0]
        if path == "/api/reboot":
            self.send_json({"ok": True})
            subprocess.Popen(["shutdown", "-r", "now"])
        elif path == "/api/shutdown":
            self.send_json({"ok": True})
            subprocess.Popen(["shutdown", "-h", "now"])
        else:
            self.send_response(404)
            self.end_headers()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    server = HTTPServer((args.host, args.port), StatusHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[STATUS] Stopped.")
