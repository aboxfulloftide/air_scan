#!/usr/bin/env python3
"""
Mobile WiFi Scanner
Wardriving/survey scanner with GPS tagging.

- USB WiFi adapter in monitor mode for scanning (pass as first arg, default wlan1)
- GPS via gpsd daemon (fallback: auto-detected serial NMEA)
- Local SQLite storage on USB drive (auto-detected or --storage PATH)
- Best RSSI per device per 10s snapshot, tagged with GPS position at snapshot time
- Live dict cleared each snapshot window (each location gets its own readings)
- Sync to central DB separately via mobile_sync.py when ethernet is up
- --both-interfaces flag reserved for future dual-interface support

All timestamps UTC.
"""

import sys
import signal
import socket
import subprocess
import threading
import time
import json
import os
import sqlite3
import argparse
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Load .env
# ---------------------------------------------------------------------------

_env_file = Path(__file__).resolve().parent.parent / ".env"
if _env_file.exists():
    with open(_env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                val = val.strip().strip('"').strip("'")
                os.environ.setdefault(key.strip(), val)

from scapy.all import sniff, Dot11, Dot11Beacon, Dot11ProbeReq, Dot11Elt, RadioTap, conf

# ---------------------------------------------------------------------------
# Args
# ---------------------------------------------------------------------------

parser = argparse.ArgumentParser(description="Mobile WiFi scanner with GPS")
parser.add_argument("iface", nargs="?", default="wlan1",
                    help="Monitor-mode interface to scan on (default: wlan1)")
parser.add_argument("--storage", default=None,
                    help="Path for SQLite DB (default: auto-detect USB drive)")
parser.add_argument("--both-interfaces", action="store_true",
                    help="(Reserved) Enable onboard WiFi alongside USB card")
parser.add_argument("--no-record", action="store_true",
                    help="Scan and print but do not write to DB (testing mode)")
args = parser.parse_args()

SCAN_IFACE       = args.iface
USE_BOTH         = args.both_interfaces   # reserved; onboard not yet activated
NO_RECORD        = args.no_record
HOSTNAME         = socket.gethostname()
SLOT_SECONDS          = 10
CYCLE_SECONDS         = 60
SPEED_THRESHOLD_MPS   = 15 * 0.44704   # 15 mph → 2.4 GHz only above this
DB_FILENAME      = "mobile_scan.db"

if USE_BOTH:
    print("[INFO] --both-interfaces noted; onboard WiFi scanning not yet implemented — USB only")

# ---------------------------------------------------------------------------
# Storage: find or use USB drive
# ---------------------------------------------------------------------------

def find_usb_storage():
    """Return first writable non-root mount point under /media or /mnt."""
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
            # Skip root device
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
    print(f"[INFO] --no-record: writing live data to {DB_PATH} (not synced to USB)")
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
# SQLite setup
# ---------------------------------------------------------------------------

def open_db():
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db(conn):
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
    conn.commit()


db_conn = open_db()
init_db(db_conn)

db_lock = threading.Lock()

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
# GPS reader
# ---------------------------------------------------------------------------

gps_state = {
    "lat":         None,
    "lon":         None,
    "fix":         False,
    "speed_mps":   None,
    "last_update": None,
}
gps_lock = threading.Lock()


def _parse_nmea_coord(value, hemisphere):
    """Convert NMEA DDDMM.MMMM + N/S/E/W to decimal degrees."""
    if not value:
        return None
    try:
        dot = value.index(".")
        deg = float(value[:dot - 2])
        minutes = float(value[dot - 2:])
        decimal = deg + minutes / 60.0
        if hemisphere in ("S", "W"):
            decimal = -decimal
        return decimal
    except Exception:
        return None


def _parse_gprmc(sentence):
    """Return (lat, lon, fix, speed_mps) or None from $GPRMC sentence."""
    try:
        parts = sentence.split(",")
        if len(parts) < 7:
            return None
        status = parts[2]   # A=active/fix, V=void
        if status != "A":
            return None, None, False, None
        lat = _parse_nmea_coord(parts[3], parts[4])
        lon = _parse_nmea_coord(parts[5], parts[6])
        speed_knots = float(parts[7]) if parts[7] else 0.0
        return lat, lon, True, speed_knots * 0.514444
    except Exception:
        return None, None, False, None


def _gps_from_gpsd():
    """Connect to gpsd and yield (lat, lon, fix, speed_mps) continuously."""
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
                    lat   = obj.get("lat")
                    lon   = obj.get("lon")
                    mode  = obj.get("mode", 0)   # 2=2D fix, 3=3D fix
                    speed = obj.get("speed")      # m/s
                    yield lat, lon, mode >= 2, speed
    except Exception as e:
        raise RuntimeError(f"gpsd unavailable: {e}")


def _find_serial_gps():
    """Return (port, baud) for the first serial device that outputs NMEA."""
    import serial
    candidates = sorted(
        list(Path("/dev").glob("ttyUSB*")) +
        list(Path("/dev").glob("ttyACM*"))
    )
    for port in candidates:
        for baud in (9600, 4800, 115200):
            try:
                with serial.Serial(str(port), baud, timeout=2) as ser:
                    for _ in range(10):
                        line = ser.readline().decode(errors="replace").strip()
                        if line.startswith("$GP") or line.startswith("$GN"):
                            print(f"[GPS] Found NMEA device: {port} @ {baud} baud")
                            return str(port), baud
            except Exception:
                continue
    return None, None


def _gps_from_serial(port, baud):
    """Yield (lat, lon, fix, speed_mps) from serial NMEA stream."""
    import serial
    with serial.Serial(port, baud, timeout=2) as ser:
        while True:
            try:
                line = ser.readline().decode(errors="replace").strip()
            except Exception:
                time.sleep(0.1)
                continue
            if line.startswith("$GPRMC") or line.startswith("$GNRMC"):
                # Strip checksum
                if "*" in line:
                    line = line[:line.index("*")]
                lat, lon, fix, speed = _parse_gprmc(line)
                yield lat, lon, fix, speed


def gps_reader():
    """Background thread: keep gps_state updated."""
    while True:
        # Try gpsd first
        try:
            print("[GPS] Connecting to gpsd...")
            for lat, lon, fix, speed in _gps_from_gpsd():
                with gps_lock:
                    if lat is not None:
                        gps_state["lat"]       = lat
                        gps_state["lon"]       = lon
                        gps_state["fix"]       = fix
                        gps_state["speed_mps"] = speed
                        gps_state["last_update"] = time.time()
            print("[GPS] gpsd connection lost — retrying")
        except Exception as e:
            print(f"[GPS] gpsd failed ({e}) — trying serial NMEA")

        # Fall back to serial
        try:
            port, baud = _find_serial_gps()
            if port:
                for lat, lon, fix, speed in _gps_from_serial(port, baud):
                    with gps_lock:
                        if lat is not None:
                            gps_state["lat"]       = lat
                            gps_state["lon"]       = lon
                            gps_state["fix"]       = fix
                            gps_state["speed_mps"] = speed
                            gps_state["last_update"] = time.time()
            else:
                print("[GPS] No serial NMEA device found — retrying in 10s")
        except Exception as e:
            print(f"[GPS] Serial error: {e}")

        time.sleep(10)


def get_gps_snapshot():
    """Return current GPS state dict (copy)."""
    with gps_lock:
        stale = (gps_state["last_update"] is None or
                 time.time() - gps_state["last_update"] > 30)
        return {
            "lat":       gps_state["lat"],
            "lon":       gps_state["lon"],
            "fix":       gps_state["fix"] and not stale,
            "speed_mps": gps_state["speed_mps"],
        }


# ---------------------------------------------------------------------------
# Band / channel schedule (same deterministic logic as wifi_scanner.py)
# ---------------------------------------------------------------------------

BAND_FREQS = {
    "2.4": [2412, 2417, 2422, 2427, 2432, 2437, 2442, 2447, 2452, 2457, 2462],
    "5":   [5180, 5200, 5220, 5240, 5745, 5765, 5785, 5805],
    "6":   [5955, 5975, 5995, 6015, 6355, 6375, 6395, 6415,
            6535, 6555, 6575, 6595, 6695, 6715, 6735, 6755],
}


def detect_supported_bands(iface):
    try:
        r = subprocess.run(["iw", "dev", iface, "info"], capture_output=True, text=True)
        phy = None
        for line in r.stdout.splitlines():
            if "wiphy" in line:
                phy = "phy" + line.strip().split()[-1]
                break
        if not phy:
            return ["2.4"]

        r = subprocess.run(["iw", "phy", phy, "info"], capture_output=True, text=True)
        freqs = []
        for line in r.stdout.splitlines():
            if "MHz [" in line and "disabled" not in line:
                try:
                    freqs.append(float(line.strip().split()[1]))
                except (IndexError, ValueError):
                    pass

        candidate_bands = []
        if any(2400 <= f <= 2500 for f in freqs): candidate_bands.append("2.4")
        if any(5000 <= f <= 5900 for f in freqs): candidate_bands.append("5")
        if any(5900 <= f <= 7300 for f in freqs): candidate_bands.append("6")

        working_bands = []
        for band in candidate_bands:
            test_freq = BAND_FREQS[band][0]
            r = subprocess.run(
                ["iw", "dev", iface, "set", "freq", str(test_freq)],
                capture_output=True, text=True
            )
            if r.returncode == 0:
                working_bands.append(band)
                print(f"[BAND] {band}GHz OK ({test_freq} MHz)")
            else:
                print(f"[BAND] {band}GHz not available — {r.stderr.strip()}")

        return working_bands if working_bands else ["2.4"]

    except Exception as e:
        print(f"[WARN] Band detection failed: {e} — defaulting to 2.4GHz only")
        return ["2.4"]


def build_schedule(bands):
    slots_per_band = (CYCLE_SECONDS // SLOT_SECONDS) // len(bands)
    schedule = []
    for band in bands:
        schedule.extend([band] * slots_per_band)
    return schedule


def get_target_freq(utc_unix, schedule, slots_per_band):
    slot   = (int(utc_unix) % CYCLE_SECONDS) // SLOT_SECONDS
    band   = schedule[slot]
    hop    = slot % slots_per_band
    minute = int(utc_unix) // CYCLE_SECONDS
    ch_idx = (minute * slots_per_band + hop) % len(BAND_FREQS[band])
    return band, BAND_FREQS[band][ch_idx]


def set_freq(iface, freq_mhz):
    r = subprocess.run(
        ["iw", "dev", iface, "set", "freq", str(freq_mhz)],
        capture_output=True, text=True
    )
    if r.returncode != 0:
        print(f"\n[HOP] Failed to set {freq_mhz} MHz: {r.stderr.strip()}")
        return False
    return True


# ---------------------------------------------------------------------------
# Shared scan state
# ---------------------------------------------------------------------------

scan_lock    = threading.Lock()
live         = {}   # mac -> best observation in current window
seen         = {}   # mac -> device metadata (persists across windows)
current_band = {"band": "?", "freq": 0}

last_packet_time = {"t": time.time()}   # updated on every received packet
STALL_TIMEOUT = 30   # seconds without a packet before we reset the interface


# ---------------------------------------------------------------------------
# Packet parsing helpers
# ---------------------------------------------------------------------------

def now_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)

def next_boundary(interval):
    now = time.time()
    return interval - (now % interval)

def get_signal(pkt):
    try:    return pkt[RadioTap].dBm_AntSignal
    except: return None

def get_freq_and_flags(pkt):
    freq, flags = None, None
    try:    freq  = int(pkt[RadioTap].ChannelFrequency)
    except: pass
    try:    flags = str(pkt[RadioTap].ChannelFlags)
    except: pass
    return freq, flags

def freq_to_channel(freq):
    if not freq:
        return None
    freq = int(freq)
    if 2412 <= freq <= 2484:
        return 14 if freq == 2484 else (freq - 2407) // 5
    elif 5180 <= freq <= 5825:
        return (freq - 5000) // 5
    elif 5955 <= freq <= 7115:
        return (freq - 5950) // 5
    return None

def get_channel(pkt):
    elt = pkt[Dot11Elt] if pkt.haslayer(Dot11Elt) else None
    while elt and isinstance(elt, Dot11Elt):
        if elt.ID == 3:
            return elt.info[0] if isinstance(elt.info, bytes) else ord(elt.info)
        elt = elt.payload if isinstance(getattr(elt, "payload", None), Dot11Elt) else None
    return None

def get_caps_and_vendors(pkt):
    ht = vht = he = False
    vendor_ouis = set()
    elt = pkt[Dot11Elt] if pkt.haslayer(Dot11Elt) else None
    while elt and isinstance(elt, Dot11Elt):
        if   elt.ID == 45:  ht  = True
        elif elt.ID == 191: vht = True
        elif elt.ID == 255 and elt.info and elt.info[0] == 35: he = True
        elif elt.ID == 221 and elt.info and len(elt.info) >= 3:
            vendor_ouis.add(":".join(f"{b:02x}" for b in elt.info[:3]))
        elt = elt.payload if isinstance(getattr(elt, "payload", None), Dot11Elt) else None
    return ht, vht, he, vendor_ouis

def get_ssid(pkt):
    """Extract SSID from Dot11Elt ID 0 only. Returns empty string if not found or invalid."""
    elt = pkt[Dot11Elt] if pkt.haslayer(Dot11Elt) else None
    while elt and isinstance(elt, Dot11Elt):
        if elt.ID == 0:
            raw = elt.info
            if not raw or len(raw) > 32:
                return ""
            try:
                ssid = raw.decode("utf-8")
            except UnicodeDecodeError:
                return ""
            if not ssid or not ssid.isprintable() or "\x00" in ssid:
                return ""
            return ssid
        elt = elt.payload if isinstance(getattr(elt, "payload", None), Dot11Elt) else None
    return ""


def is_valid_ssid(ssid):
    """Check if an SSID is valid for storage — printable, 1-32 chars, no garbage."""
    if not ssid or len(ssid) > 32:
        return False
    if "\ufffd" in ssid:
        return False
    if not ssid.isprintable():
        return False
    if "\x00" in ssid:
        return False
    alnum = sum(1 for c in ssid if c.isalnum() or c in ' -_.')
    if len(ssid) > 4 and alnum / len(ssid) < 0.3:
        return False
    return True


def get_oui(mac):
    return mac[:8].upper()

def mac_is_randomized(mac):
    try:    return bool(int(mac.split(":")[0], 16) & 0x02)
    except: return False

def get_manufacturer(mac):
    try:
        m = conf.manufdb.get_manuf(mac)
        return m if m else None
    except: return None


# ---------------------------------------------------------------------------
# Packet handler — keeps best RSSI per MAC in current window
# ---------------------------------------------------------------------------

def clean_ssid(raw_bytes):
    """Decode SSID bytes and return a clean printable string, or '' if garbage."""
    if not raw_bytes:
        return ""
    try:
        s = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        try:
            s = raw_bytes.decode("latin-1")
        except Exception:
            return ""
    # Drop if any character is a control character (except space)
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in s):
        return ""
    # Drop if more than half the characters are non-ASCII (likely binary garbage)
    non_ascii = sum(1 for c in s if ord(c) > 0x7E)
    if non_ascii > len(s) / 2:
        return ""
    return s.strip()


def reset_monitor(iface):
    """Cycle interface back into monitor mode after a stall."""
    subprocess.run(["ip", "link", "set", iface, "down"],  capture_output=True)
    subprocess.run(["iw", "dev", iface, "set", "type", "monitor"], capture_output=True)
    subprocess.run(["ip", "link", "set", iface, "up"],    capture_output=True)
    freq = current_band.get("freq")
    if freq:
        subprocess.run(["iw", "dev", iface, "set", "freq", str(freq)], capture_output=True)


def watchdog(iface):
    """Restart monitor mode if no packets received for STALL_TIMEOUT seconds."""
    while True:
        time.sleep(STALL_TIMEOUT)
        elapsed = time.time() - last_packet_time["t"]
        if elapsed >= STALL_TIMEOUT:
            print(f"\n[WATCHDOG] No packets for {elapsed:.0f}s — resetting {iface} monitor mode")
            reset_monitor(iface)
            last_packet_time["t"] = time.time()



def handle_packet(pkt):
    last_packet_time["t"] = time.time()
    if not pkt.haslayer(Dot11):
        return

    sig         = get_signal(pkt)
    freq, flags = get_freq_and_flags(pkt)
    ts          = now_utc()
    mac = device_type = ssid = channel = None
    ht = vht = he = False
    vendor_ouis = set()

    if pkt.haslayer(Dot11Beacon):
        mac         = pkt[Dot11].addr3
        device_type = "AP"
        ssid        = get_ssid(pkt)
        channel     = get_channel(pkt)
        ht, vht, he, vendor_ouis = get_caps_and_vendors(pkt)
    elif pkt.haslayer(Dot11ProbeReq):
        mac         = pkt[Dot11].addr2
        device_type = "Client"
        ssid        = get_ssid(pkt)
        ht, vht, he, vendor_ouis = get_caps_and_vendors(pkt)

    if not mac or mac == "ff:ff:ff:ff:ff:ff":
        return

    if channel is None:
        channel = freq_to_channel(freq)

    oui        = get_oui(mac)
    randomized = mac_is_randomized(mac)

    with scan_lock:
        # Update persistent device metadata
        if mac not in seen:
            seen[mac] = {
                "type": device_type, "ssids": set(),
                "first_seen": ts, "last_seen": ts,
                "oui": oui, "manufacturer": get_manufacturer(mac),
                "is_randomized": randomized,
                "ht": False, "vht": False, "he": False, "vendor_ouis": set(),
            }
        dev = seen[mac]
        dev["last_seen"] = ts
        if ssid:           dev["ssids"].add(ssid)
        if ht:             dev["ht"]  = True
        if vht:            dev["vht"] = True
        if he:             dev["he"]  = True
        dev["vendor_ouis"].update(vendor_ouis)

        # Track best (highest) RSSI in the current window
        if mac not in live:
            live[mac] = {
                "type": device_type, "signal": sig,
                "channel": channel, "freq_mhz": freq, "channel_flags": flags,
                "ssids": set(), "ht": ht, "vht": vht, "he": he,
                "vendor_ouis": set(), "oui": oui,
                "is_randomized": randomized, "manufacturer": dev["manufacturer"],
            }
        else:
            # Keep highest RSSI seen this window
            if sig is not None:
                if live[mac]["signal"] is None or sig > live[mac]["signal"]:
                    live[mac]["signal"]       = sig
                    live[mac]["channel"]      = channel or live[mac]["channel"]
                    live[mac]["freq_mhz"]     = freq    or live[mac]["freq_mhz"]
                    live[mac]["channel_flags"] = flags  or live[mac]["channel_flags"]
            if ht:  live[mac]["ht"]  = True
            if vht: live[mac]["vht"] = True
            if he:  live[mac]["he"]  = True
            live[mac]["vendor_ouis"].update(vendor_ouis)

        if ssid:
            live[mac]["ssids"].add(ssid)


# ---------------------------------------------------------------------------
# Channel hopper
# ---------------------------------------------------------------------------

def channel_hopper(iface, schedule_dual, spb_dual, schedule_24, spb_24):
    time.sleep(max(0, next_boundary(SLOT_SECONDS) - 0.2))
    while True:
        with gps_lock:
            speed = gps_state["speed_mps"] or 0
        if speed >= SPEED_THRESHOLD_MPS:
            schedule, slots_per_band = schedule_24, spb_24
        else:
            schedule, slots_per_band = schedule_dual, spb_dual
        band, freq = get_target_freq(time.time(), schedule, slots_per_band)
        ok = set_freq(iface, freq)
        with scan_lock:
            current_band["band"] = band
            current_band["freq"] = freq if ok else current_band["freq"]
        time.sleep(max(0, next_boundary(SLOT_SECONDS) - 0.2))


# ---------------------------------------------------------------------------
# Snapshot + SQLite writer
# ---------------------------------------------------------------------------

def write_snapshot(snap, gps, ts):
    """Write a snapshot to SQLite. snap is a dict of mac -> observation data."""
    if not snap:
        return

    with db_lock:
        cur = db_conn.cursor()
        ts_str = ts.isoformat()

        for mac, s in snap.items():
            dev = seen.get(mac, s)

            # Upsert device
            cur.execute("""
                INSERT INTO devices
                    (mac, device_type, oui, manufacturer, is_randomized,
                     ht_capable, vht_capable, he_capable, first_seen, last_seen)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(mac) DO UPDATE SET
                    last_seen   = MAX(last_seen, excluded.last_seen),
                    ht_capable  = MAX(ht_capable,  excluded.ht_capable),
                    vht_capable = MAX(vht_capable, excluded.vht_capable),
                    he_capable  = MAX(he_capable,  excluded.he_capable)
            """, (
                mac,
                dev.get("type", s["type"]),
                s["oui"], s["manufacturer"],
                int(s.get("is_randomized", False)),
                int(s.get("ht", False)),
                int(s.get("vht", False)),
                int(s.get("he", False)),
                dev.get("first_seen", ts).isoformat() if hasattr(dev.get("first_seen", ts), "isoformat") else str(dev.get("first_seen", ts_str)),
                ts_str,
            ))

            for ssid in s.get("ssids", set()):
                if is_valid_ssid(ssid):
                    cur.execute(
                        "INSERT OR IGNORE INTO ssids (mac, ssid, first_seen) VALUES (?, ?, ?)",
                        (mac, ssid, ts_str)
                    )

            for voui in s.get("vendor_ouis", set()):
                cur.execute(
                    "INSERT OR IGNORE INTO vendor_ies (mac, vendor_oui, first_seen) VALUES (?, ?, ?)",
                    (mac, voui, ts_str)
                )

            cur.execute("""
                INSERT INTO observations
                    (session_id, mac, interface, scanner_host,
                     signal_dbm, channel, freq_mhz, channel_flags,
                     gps_lat, gps_lon, gps_fix, recorded_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                SESSION_ID, mac, SCAN_IFACE, HOSTNAME,
                s["signal"], s.get("channel"), s.get("freq_mhz"), s.get("channel_flags"),
                gps["lat"], gps["lon"], int(gps["fix"]),
                ts_str,
            ))

        db_conn.commit()


def snapshot_thread():
    time.sleep(next_boundary(SLOT_SECONDS))
    while True:
        ts  = now_utc()
        gps = get_gps_snapshot()

        with scan_lock:
            snap = {mac: dict(v) for mac, v in live.items()}
            live.clear()   # reset window — each 10s gets its own best readings

        write_snapshot(snap, gps, ts)

        gps_str = (f"{gps['lat']:.6f},{gps['lon']:.6f}" if gps["lat"] else "no fix")
        fix_marker = "" if gps["fix"] else " (stale)"
        speed_mps = gps["speed_mps"] or 0
        speed_mph = speed_mps * 2.23694
        speed_str = f"{speed_mph:.1f}mph"
        band_mode = "2.4only" if speed_mps >= SPEED_THRESHOLD_MPS else "dual"
        print(
            f"\r[{ts.strftime('%H:%M:%S')} UTC]  "
            f"{current_band['band']}GHz @ {current_band['freq']}MHz [{band_mode}]  |  "
            f"GPS: {gps_str}{fix_marker} {speed_str}  |  "
            f"Window: {len(snap)}  Total: {len(seen)}   ",
            end="", flush=True
        )

        time.sleep(next_boundary(SLOT_SECONDS))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def on_exit(sig, frame):
    print(f"\n\nShutting down — total devices seen: {len(seen)}")
    if not NO_RECORD:
        end_session(db_conn, SESSION_ID)
        print(f"Data stored at: {DB_PATH}")
    sys.exit(0)


if __name__ == "__main__":
    signal.signal(signal.SIGINT,  on_exit)
    signal.signal(signal.SIGTERM, on_exit)

    print(f"Mobile Scanner  : {HOSTNAME}/{SCAN_IFACE}")
    print(f"Storage         : {DB_PATH}")
    print(f"Session ID      : {SESSION_ID}")

    bands          = detect_supported_bands(SCAN_IFACE)
    schedule_dual  = build_schedule(bands)
    spb_dual       = (CYCLE_SECONDS // SLOT_SECONDS) // len(bands)
    schedule_24    = build_schedule(["2.4"])
    spb_24         = (CYCLE_SECONDS // SLOT_SECONDS) // 1

    print(f"Bands           : {', '.join(b + 'GHz' for b in bands)}  (speed < 15mph: dual, >= 15mph: 2.4GHz only)")
    print(f"All times UTC — Ctrl+C to stop\n")

    band0, freq0 = get_target_freq(time.time(), schedule_dual, spb_dual)
    set_freq(SCAN_IFACE, freq0)
    current_band["band"] = band0
    current_band["freq"] = freq0

    threading.Thread(target=gps_reader,    daemon=True).start()
    threading.Thread(target=channel_hopper, args=(SCAN_IFACE, schedule_dual, spb_dual, schedule_24, spb_24), daemon=True).start()
    threading.Thread(target=snapshot_thread, daemon=True).start()
    threading.Thread(target=watchdog, args=(SCAN_IFACE,), daemon=True).start()

    sniff(iface=SCAN_IFACE, prn=handle_packet, store=False)
