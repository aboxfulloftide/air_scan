#!/usr/bin/env python3
"""
Mobile BLE Scanner
Passive BLE advertisement scanner with GPS tagging.

- Onboard Bluetooth (hci0) in passive scan mode
- GPS via gpsd (same as WiFi scanner)
- Best RSSI per device per 10s snapshot, tagged with GPS position
- Writes to the same SQLite DB as mobile_scanner.py
- device_type = 'BLE' in devices table
- --no-record flag for test mode (writes to /tmp)

All timestamps UTC.
"""

import asyncio
import argparse
import json
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from ble_classify import classify_tracker

# ---------------------------------------------------------------------------
# Args
# ---------------------------------------------------------------------------

parser = argparse.ArgumentParser(description="Mobile BLE scanner with GPS")
parser.add_argument("--storage", default=None,
                    help="Path for SQLite DB directory (default: auto-detect USB)")
parser.add_argument("--iface", default="hci0",
                    help="HCI interface to scan on (default: hci0)")
parser.add_argument("--no-record", action="store_true",
                    help="Scan and print but do not write to DB (test mode)")
args = parser.parse_args()

SCAN_IFACE   = args.iface
NO_RECORD    = args.no_record
HOSTNAME     = socket.gethostname()
SLOT_SECONDS = 10
DB_FILENAME  = "mobile_scan.db"

def _load_ignored_macs():
    """Load MAC ignore list from ignore.json alongside the DB."""
    try:
        data = json.loads((DB_PATH.parent / "ignore.json").read_text())
        return {m.lower() for m in data.get("macs", [])}
    except Exception:
        return set()

_ignored_macs = _load_ignored_macs()
if _ignored_macs:
    print(f"[BLE] Ignoring {len(_ignored_macs)} MAC(s) from ignore.json")

# ---------------------------------------------------------------------------
# Storage path (mirrors mobile_scanner.py logic)
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


if NO_RECORD:
    storage_dir = Path("/tmp/air_scan")
    storage_dir.mkdir(parents=True, exist_ok=True)
    DB_PATH = storage_dir / DB_FILENAME
    print(f"[INFO] --no-record: writing to {DB_PATH}")
else:
    if args.storage:
        storage_dir = Path(args.storage)
    else:
        usb = find_usb_storage()
        if usb:
            storage_dir = usb / "air_scan"
        else:
            storage_dir = Path("/tmp/air_scan")
            print(f"[WARN] No USB drive found — storing locally at {storage_dir}")
    storage_dir.mkdir(parents=True, exist_ok=True)
    DB_PATH = storage_dir / DB_FILENAME

# ---------------------------------------------------------------------------
# SQLite — reuses the shared schema, adds BLE columns if missing
# ---------------------------------------------------------------------------

def open_db():
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db(conn):
    # Core tables identical to mobile_scanner.py
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            scanner_host TEXT NOT NULL,
            scan_iface  TEXT NOT NULL,
            started_at  TEXT NOT NULL,
            ended_at    TEXT,
            synced      INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS devices (
            mac             TEXT PRIMARY KEY,
            device_type     TEXT NOT NULL,
            oui             TEXT,
            manufacturer    TEXT,
            is_randomized   INTEGER NOT NULL DEFAULT 0,
            ht_capable      INTEGER NOT NULL DEFAULT 0,
            vht_capable     INTEGER NOT NULL DEFAULT 0,
            he_capable      INTEGER NOT NULL DEFAULT 0,
            first_seen      TEXT NOT NULL,
            last_seen       TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS ssids (
            mac         TEXT NOT NULL,
            ssid        TEXT NOT NULL,
            first_seen  TEXT NOT NULL,
            PRIMARY KEY (mac, ssid)
        );

        CREATE TABLE IF NOT EXISTS vendor_ies (
            mac         TEXT NOT NULL,
            vendor_oui  TEXT NOT NULL,
            first_seen  TEXT NOT NULL,
            PRIMARY KEY (mac, vendor_oui)
        );

        CREATE TABLE IF NOT EXISTS observations (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id      INTEGER NOT NULL,
            mac             TEXT NOT NULL,
            interface       TEXT NOT NULL,
            scanner_host    TEXT NOT NULL,
            signal_dbm      INTEGER,
            channel         INTEGER,
            freq_mhz        INTEGER,
            channel_flags   TEXT,
            gps_lat         REAL,
            gps_lon         REAL,
            gps_fix         INTEGER NOT NULL DEFAULT 0,
            recorded_at     TEXT NOT NULL,
            synced          INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (session_id) REFERENCES sessions(id)
        );

        CREATE INDEX IF NOT EXISTS idx_obs_session   ON observations(session_id);
        CREATE INDEX IF NOT EXISTS idx_obs_synced    ON observations(synced);
        CREATE INDEX IF NOT EXISTS idx_obs_recorded  ON observations(recorded_at);
    """)

    # BLE-specific columns (added if not already present — safe on existing DBs)
    for col, typedef in [
        ("adv_type",          "TEXT"),
        ("manufacturer_data", "TEXT"),
        ("adv_services",      "TEXT"),
        ("adv_service_data",  "TEXT"),
        ("tx_power",          "INTEGER"),
        ("tracker_type",      "TEXT"),
    ]:
        try:
            conn.execute(f"ALTER TABLE observations ADD COLUMN {col} {typedef}")
        except sqlite3.OperationalError:
            pass   # column already exists

    conn.commit()


db_conn  = open_db()
init_db(db_conn)
db_lock  = threading.Lock()


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------

def start_session(conn):
    ts = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    cur = conn.execute(
        "INSERT INTO sessions (scanner_host, scan_iface, started_at) VALUES (?, ?, ?)",
        (HOSTNAME, SCAN_IFACE, ts)
    )
    conn.commit()
    return cur.lastrowid


def end_session(conn, session_id):
    ts = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    conn.execute("UPDATE sessions SET ended_at = ? WHERE id = ?", (ts, session_id))
    conn.commit()


SESSION_ID = start_session(db_conn)

# ---------------------------------------------------------------------------
# GPS reader (identical to mobile_scanner.py)
# ---------------------------------------------------------------------------

gps_state = {
    "lat":         None,
    "lon":         None,
    "fix":         False,
    "last_update": None,
}
gps_lock = threading.Lock()


def _gps_from_gpsd():
    import socket as _socket
    try:
        s = _socket.create_connection(("127.0.0.1", 2947), timeout=3)
        s.sendall(b'?WATCH={"enable":true,"json":true}\n')
        buf = ""
        while True:
            chunk = s.recv(4096).decode(errors="replace")
            if not chunk:
                break
            buf += chunk
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("class") == "TPV":
                    lat  = obj.get("lat")
                    lon  = obj.get("lon")
                    mode = obj.get("mode", 0)
                    yield lat, lon, mode >= 2
    except Exception as e:
        raise RuntimeError(f"gpsd unavailable: {e}")


def gps_reader():
    while True:
        try:
            print("[GPS] Connecting to gpsd...")
            for lat, lon, fix in _gps_from_gpsd():
                with gps_lock:
                    if lat is not None:
                        gps_state["lat"]         = lat
                        gps_state["lon"]         = lon
                        gps_state["fix"]         = fix
                        gps_state["last_update"] = time.time()
            print("[GPS] gpsd connection lost — retrying in 10s")
        except Exception as e:
            print(f"[GPS] {e} — retrying in 10s")
        time.sleep(10)


def get_gps_snapshot():
    with gps_lock:
        stale = (gps_state["last_update"] is None or
                 time.time() - gps_state["last_update"] > 30)
        return {
            "lat": gps_state["lat"],
            "lon": gps_state["lon"],
            "fix": gps_state["fix"] and not stale,
        }


# ---------------------------------------------------------------------------
# OUI / manufacturer lookup
# ---------------------------------------------------------------------------

def get_oui(mac):
    return mac[:8].upper()


def mac_is_randomized(mac):
    """BLE random addresses have bit 6 of first byte set."""
    try:
        return bool(int(mac.split(":")[0], 16) & 0x40)
    except Exception:
        return False


def get_manufacturer(mac):
    try:
        from scapy.all import conf as scapy_conf
        m = scapy_conf.manufdb.get_manuf(mac)
        return m if m else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# BLE scan state
# ---------------------------------------------------------------------------

scan_lock = threading.Lock()
live  = {}   # mac -> best observation in current window
seen  = {}   # mac -> device metadata (persists across windows)

# BLE advertising channels map to fixed frequencies
# ch37=2402MHz, ch38=2426MHz, ch39=2480MHz (rotated per advertisement)
BLE_ADV_CHANNELS = [37, 38, 39]
BLE_ADV_FREQS    = [2402, 2426, 2480]


def _adv_type_str(adv_type):
    """Convert bleak AdvertisementData connectable/etc to a short string."""
    # bleak doesn't expose adv_type directly; we derive from connectable
    return None


def on_advertisement(device, adv_data):
    """Called by bleak for every BLE advertisement received."""
    mac  = device.address.lower()
    if mac in _ignored_macs:
        return
    rssi = adv_data.rssi
    ts   = datetime.now(timezone.utc).replace(tzinfo=None)

    # Manufacturer data: encode as "XXXX:<hex>" per entry
    mfr_dict  = adv_data.manufacturer_data or {}
    mfr_parts = [f"{cid:04X}:{d.hex()}" for cid, d in mfr_dict.items()]
    mfr_str   = ",".join(mfr_parts) if mfr_parts else None

    # Advertised service UUIDs
    svc_uuids = adv_data.service_uuids or []
    svc_str   = ",".join(str(u) for u in svc_uuids) or None

    # Service data (UUID -> bytes payload) — needed for FMDN / Eddystone
    svc_data_dict = adv_data.service_data or {}
    svc_data_parts = [f"{uuid}:{data.hex()}" for uuid, data in svc_data_dict.items()]
    svc_data_str  = ",".join(svc_data_parts) if svc_data_parts else None

    tx_power = adv_data.tx_power   # may be None

    tracker = classify_tracker(mfr_dict, svc_uuids, svc_data_dict)

    # Use local name as "SSID" equivalent
    name = adv_data.local_name or ""

    randomized = mac_is_randomized(mac)
    oui        = get_oui(mac)

    with scan_lock:
        if mac not in seen:
            seen[mac] = {
                "type":         "BLE",
                "first_seen":   ts,
                "last_seen":    ts,
                "oui":          oui,
                "manufacturer": get_manufacturer(mac),
                "is_randomized": randomized,
                "names":        set(),
                "tracker_type": tracker,
            }
            if tracker:
                print(f"[TRACKER] {tracker:20s}  {mac}  rssi={rssi}")
        dev = seen[mac]
        dev["last_seen"] = ts
        # Update tracker_type if we now have a classification
        if tracker and not dev.get("tracker_type"):
            dev["tracker_type"] = tracker
            print(f"[TRACKER] {tracker:20s}  {mac}  rssi={rssi}")
        if name:
            dev["names"].add(name)

        if mac not in live:
            live[mac] = {
                "signal":            rssi,
                "manufacturer_data": mfr_str,
                "adv_services":      svc_str,
                "adv_service_data":  svc_data_str,
                "tx_power":          tx_power,
                "tracker_type":      tracker or dev.get("tracker_type"),
                "names":             set(),
                "oui":               oui,
                "is_randomized":     randomized,
                "manufacturer":      dev["manufacturer"],
            }
        else:
            if rssi is not None:
                if live[mac]["signal"] is None or rssi > live[mac]["signal"]:
                    live[mac]["signal"] = rssi
            # Accumulate data across advertisements
            if mfr_str and not live[mac]["manufacturer_data"]:
                live[mac]["manufacturer_data"] = mfr_str
            if svc_str:
                existing = live[mac]["adv_services"] or ""
                new_svcs = set(existing.split(",")) | set(svc_str.split(","))
                new_svcs.discard("")
                live[mac]["adv_services"] = ",".join(sorted(new_svcs)) or None
            if svc_data_str and not live[mac]["adv_service_data"]:
                live[mac]["adv_service_data"] = svc_data_str
            if tx_power is not None and live[mac]["tx_power"] is None:
                live[mac]["tx_power"] = tx_power
            if tracker and not live[mac].get("tracker_type"):
                live[mac]["tracker_type"] = tracker

        if name:
            live[mac]["names"].add(name)


# ---------------------------------------------------------------------------
# Snapshot writer
# ---------------------------------------------------------------------------

def write_snapshot(snap, gps, ts):
    if not snap:
        return

    with db_lock:
        cur = db_conn.cursor()
        ts_str = ts.isoformat()

        for mac, s in snap.items():
            dev = seen.get(mac, s)

            cur.execute("""
                INSERT INTO devices
                    (mac, device_type, oui, manufacturer, is_randomized,
                     ht_capable, vht_capable, he_capable, first_seen, last_seen)
                VALUES (?, 'BLE', ?, ?, ?, 0, 0, 0, ?, ?)
                ON CONFLICT(mac) DO UPDATE SET
                    last_seen = MAX(last_seen, excluded.last_seen),
                    manufacturer = COALESCE(manufacturer, excluded.manufacturer)
            """, (
                mac,
                s["oui"], s["manufacturer"],
                int(s.get("is_randomized", False)),
                dev.get("first_seen", ts).isoformat()
                    if hasattr(dev.get("first_seen", ts), "isoformat")
                    else ts_str,
                ts_str,
            ))

            # Store BLE device name(s) in the ssids table (reused as "name" store)
            for name in s.get("names", set()):
                if name:
                    cur.execute(
                        "INSERT OR IGNORE INTO ssids (mac, ssid, first_seen) VALUES (?, ?, ?)",
                        (mac, name, ts_str)
                    )

            # BLE advertising uses channels 37/38/39 in rotation — record ch37 as representative
            cur.execute("""
                INSERT INTO observations
                    (session_id, mac, interface, scanner_host,
                     signal_dbm, channel, freq_mhz,
                     gps_lat, gps_lon, gps_fix, recorded_at,
                     manufacturer_data, adv_services, adv_service_data,
                     tx_power, tracker_type)
                VALUES (?, ?, ?, ?, ?, 37, 2402, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                SESSION_ID, mac, SCAN_IFACE, HOSTNAME,
                s["signal"],
                gps["lat"], gps["lon"], int(gps["fix"]),
                ts_str,
                s.get("manufacturer_data"),
                s.get("adv_services"),
                s.get("adv_service_data"),
                s.get("tx_power"),
                s.get("tracker_type"),
            ))

        db_conn.commit()
        total    = len(snap)
        trackers = sum(1 for s in snap.values() if s.get("tracker_type"))
        tracker_summary = f"  [{trackers} trackers]" if trackers else ""
        print(f"[SNAP] {ts_str}  {total} BLE devices{tracker_summary}  "
              f"GPS {'fix' if gps['fix'] else 'no-fix'} "
              f"({gps['lat']}, {gps['lon']})")


# ---------------------------------------------------------------------------
# Snapshot thread
# ---------------------------------------------------------------------------

def next_boundary(interval):
    now = time.time()
    return interval - (now % interval)


def snapshot_thread():
    global live, _ignored_macs
    time.sleep(next_boundary(SLOT_SECONDS))
    while True:
        ts  = datetime.now(timezone.utc).replace(tzinfo=None)
        gps = get_gps_snapshot()
        _ignored_macs = _load_ignored_macs()
        with scan_lock:
            snap = {mac: dict(e, names=set(e["names"])) for mac, e in live.items()
                    if mac not in _ignored_macs}
            live = {}
        if snap:
            write_snapshot(snap, gps, ts)
        time.sleep(max(0, next_boundary(SLOT_SECONDS) - 0.05))


# ---------------------------------------------------------------------------
# BLE scan loop (asyncio)
# ---------------------------------------------------------------------------

async def ble_scan_loop():
    from bleak import BleakScanner

    print(f"[BLE] Starting passive scan on {SCAN_IFACE}")
    stop_event = asyncio.Event()

    def _sigterm(*_):
        stop_event.set()

    loop = asyncio.get_event_loop()
    loop.add_signal_handler(signal.SIGTERM, _sigterm)
    loop.add_signal_handler(signal.SIGINT,  _sigterm)

    scanner = BleakScanner(
        detection_callback=on_advertisement,
        adapter=SCAN_IFACE,
        scanning_mode="active",
    )

    async with scanner:
        print(f"[BLE] Scanning… (Ctrl-C to stop)")
        await stop_event.wait()

    print("[BLE] Scan stopped.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    # GPS thread
    t = threading.Thread(target=gps_reader, daemon=True)
    t.start()

    # Snapshot thread
    t = threading.Thread(target=snapshot_thread, daemon=True)
    t.start()

    print(f"[BLE] DB: {DB_PATH}")
    print(f"[BLE] Session ID: {SESSION_ID}")

    try:
        asyncio.run(ble_scan_loop())
    finally:
        end_session(db_conn, SESSION_ID)
        db_conn.close()
        print("[BLE] Session ended.")


if __name__ == "__main__":
    main()
