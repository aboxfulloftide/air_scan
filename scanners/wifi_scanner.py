#!/usr/bin/env python3
"""
WiFi Device Scanner
Captures probe requests and beacon frames in monitor mode.
- Detects supported bands (2.4/5/6GHz) at startup and builds schedule accordingly
- 3 bands: 20s each | 2 bands: 30s each | 1 band: 60s
- All scanners derive channel from UTC time — no coordination needed
- Snapshots the most recent signal per device at aligned 10s boundaries
- Only snapshots devices heard during the current dwell window
- Flushes to MySQL every 60s
All timestamps are UTC.
"""

import sys
import signal
import socket
import subprocess
import threading
import time
import json
import os
from datetime import datetime, timezone
from pathlib import Path

# Load .env from project root
_env_file = Path(__file__).resolve().parent.parent / ".env"
if _env_file.exists():
    with open(_env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                val = val.strip().strip('"').strip("'")
                os.environ.setdefault(key.strip(), val)

import asyncio
import mysql.connector
from scapy.all import sniff, Dot11, Dot11Beacon, Dot11ProbeReq, Dot11Elt, RadioTap, conf

try:
    from bleak import BleakScanner
    HAS_BLEAK = True
except ImportError:
    HAS_BLEAK = False

from ble_classify import classify_tracker

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

IFACE             = sys.argv[1] if len(sys.argv) > 1 else "wlan1"
HOSTNAME          = socket.gethostname()
SLOT_SECONDS      = 10
CYCLE_SECONDS     = 60
FLUSH_INTERVAL    = 60
BUFFER_FILE       = "/home/matheau/scanner/offline_buffer.jsonl"

DB = {
    "host":         os.environ.get("DB_HOST", "localhost"),
    "user":         os.environ["DB_USER"],
    "password":     os.environ["DB_PASS"],
    "database":     os.environ.get("DB_NAME", "wireless"),
    "ssl_disabled": os.environ.get("DB_SSL", "disabled").lower() == "disabled",
}

BLE_IFACE         = os.environ.get("BLE_IFACE", "hci0")
BLE_ENABLED       = HAS_BLEAK and os.environ.get("BLE_SCAN", "1") != "0"

BAND_FREQS = {
    "2.4": [2412, 2437, 2462],
    "5":   [5180, 5200, 5220, 5240, 5745, 5765, 5785, 5805],
    "6":   [5955, 5975, 5995, 6015, 6355, 6375, 6395, 6415,
            6535, 6555, 6575, 6595, 6695, 6715, 6735, 6755],
}

# ---------------------------------------------------------------------------
# Band detection
# ---------------------------------------------------------------------------

def detect_supported_bands(iface):
    """
    Detect bands by:
    1. Checking which frequencies the adapter reports as available
    2. Actually testing a freq set on each band — drops any that the driver rejects
    This handles cases where hardware lists a band but the driver can't switch to it
    (e.g. 6GHz on MT7925 under current Linux regulatory restrictions).
    """
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

        # Test each band by actually trying to set a frequency
        working_bands = []
        for band in candidate_bands:
            test_freq = BAND_FREQS[band][0]
            r = subprocess.run(
                ["iw", "dev", iface, "set", "freq", str(test_freq), "HT20"],
                capture_output=True, text=True
            )
            if r.returncode != 0:
                r = subprocess.run(
                    ["iw", "dev", iface, "set", "freq", str(test_freq)],
                    capture_output=True, text=True
                )
            if r.returncode == 0:
                working_bands.append(band)
                print(f"[BAND] {band}GHz OK ({test_freq} MHz)")
            else:
                print(f"[BAND] {band}GHz not available ({test_freq} MHz) — {r.stderr.strip()}")

        return working_bands if working_bands else ["2.4"]

    except Exception as e:
        print(f"[WARN] Band detection failed: {e} — defaulting to 2.4GHz only")
        return ["2.4"]


def build_schedule(bands):
    """Divide 60s equally among available bands, one entry per 10s slot."""
    slots_per_band = (CYCLE_SECONDS // SLOT_SECONDS) // len(bands)
    schedule = []
    for band in bands:
        schedule.extend([band] * slots_per_band)
    return schedule


def get_target_freq(utc_unix, schedule, slots_per_band):
    """Deterministic channel selection from UTC time. Same result on all NTP-synced scanners."""
    slot   = (int(utc_unix) % CYCLE_SECONDS) // SLOT_SECONDS
    band   = schedule[slot]
    hop    = slot % slots_per_band
    minute = int(utc_unix) // CYCLE_SECONDS
    ch_idx = (minute * slots_per_band + hop) % len(BAND_FREQS[band])
    return band, BAND_FREQS[band][ch_idx]


def set_freq(iface, freq_mhz):
    r = subprocess.run(
        ["iw", "dev", iface, "set", "freq", str(freq_mhz), "HT20"],
        capture_output=True, text=True
    )
    if r.returncode != 0:
        # Fall back to legacy mode if HT20 not supported
        r = subprocess.run(
            ["iw", "dev", iface, "set", "freq", str(freq_mhz)],
            capture_output=True, text=True
        )
    if r.returncode != 0:
        print(f"\n[HOP] Failed to set {freq_mhz} MHz: {r.stderr.strip()}")
        return False
    return True


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

lock                 = threading.Lock()
seen                 = {}
live                 = {}
pending_observations = []
current_band         = {"band": "?", "freq": 0}

# BLE state — separate dicts, shared lock
ble_seen = {}
ble_live = {}
ble_stats = {"total": 0, "trackers": 0}


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
    """Check if an SSID is valid for storage — printable ASCII/UTF-8, 1-32 chars, no garbage."""
    if not ssid or len(ssid) > 32:
        return False
    if "\ufffd" in ssid:  # replacement character from bad decode
        return False
    if not ssid.isprintable():
        return False
    if "\x00" in ssid:
        return False
    # Reject if mostly non-alphanumeric (heuristic for binary garbage that passed other checks)
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
        m = conf.manufdb._get_manuf(mac)
        return m if m else None
    except: return None


# ---------------------------------------------------------------------------
# Packet handler
# ---------------------------------------------------------------------------

def handle_packet(pkt):
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

    with lock:
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
        if ssid: dev["ssids"].add(ssid)
        if ht:   dev["ht"]  = True
        if vht:  dev["vht"] = True
        if he:   dev["he"]  = True
        dev["vendor_ouis"].update(vendor_ouis)

        if mac not in live:
            live[mac] = {
                "type": device_type, "signal": sig,
                "channel": channel, "freq_mhz": freq, "channel_flags": flags,
                "ssids": set(), "ht": ht, "vht": vht, "he": he,
                "vendor_ouis": set(), "oui": oui,
                "is_randomized": randomized, "manufacturer": dev["manufacturer"],
                "last_heard": ts, "probe_count": 1,
            }
        else:
            live[mac]["probe_count"] = live[mac].get("probe_count", 0) + 1
            live[mac]["signal"]      = sig
            live[mac]["last_heard"]  = ts
            if channel is not None: live[mac]["channel"]       = channel
            if freq    is not None: live[mac]["freq_mhz"]      = freq
            if flags   is not None: live[mac]["channel_flags"] = flags
            if ht:  live[mac]["ht"]  = True
            if vht: live[mac]["vht"] = True
            if he:  live[mac]["he"]  = True
            live[mac]["vendor_ouis"].update(vendor_ouis)
        if ssid:
            live[mac]["ssids"].add(ssid)


# ---------------------------------------------------------------------------
# Channel hopper — fires 200ms before each snapshot boundary
# ---------------------------------------------------------------------------

def channel_hopper(schedule, slots_per_band):
    time.sleep(max(0, next_boundary(SLOT_SECONDS) - 0.2))
    while True:
        band, freq = get_target_freq(time.time(), schedule, slots_per_band)
        ok = set_freq(IFACE, freq)
        with lock:
            current_band["band"] = band
            current_band["freq"] = freq if ok else current_band["freq"]
        time.sleep(max(0, next_boundary(SLOT_SECONDS) - 0.2))


# ---------------------------------------------------------------------------
# BLE scanner
# ---------------------------------------------------------------------------

def ble_mac_is_randomized(mac):
    """BLE random addresses have bit 6 of first byte set."""
    try:    return bool(int(mac.split(":")[0], 16) & 0x40)
    except: return False


def on_ble_advertisement(device, adv_data):
    """Called by bleak for every BLE advertisement received."""
    mac  = device.address.lower()
    rssi = adv_data.rssi
    ts   = now_utc()

    # Manufacturer data: encode as "XXXX:<hex>" per entry
    mfr_dict  = adv_data.manufacturer_data or {}
    mfr_parts = [f"{cid:04X}:{d.hex()}" for cid, d in mfr_dict.items()]
    mfr_str   = ",".join(mfr_parts) if mfr_parts else None

    # Advertised service UUIDs
    svc_uuids = adv_data.service_uuids or []
    svc_str   = ",".join(str(u) for u in svc_uuids) or None

    # Service data (UUID -> bytes payload)
    svc_data_dict  = adv_data.service_data or {}
    svc_data_parts = [f"{uuid}:{data.hex()}" for uuid, data in svc_data_dict.items()]
    svc_data_str   = ",".join(svc_data_parts) if svc_data_parts else None

    tx_power = adv_data.tx_power
    tracker  = classify_tracker(mfr_dict, svc_uuids, svc_data_dict)
    name     = adv_data.local_name or ""

    randomized = ble_mac_is_randomized(mac)
    oui        = get_oui(mac)

    with lock:
        if mac not in ble_seen:
            ble_seen[mac] = {
                "type":          "BLE",
                "first_seen":    ts,
                "last_seen":     ts,
                "oui":           oui,
                "manufacturer":  get_manufacturer(mac),
                "is_randomized": randomized,
                "names":         set(),
                "tracker_type":  tracker,
            }
            ble_stats["total"] += 1
            if tracker:
                ble_stats["trackers"] += 1
                print(f"\n[BLE TRACKER] {tracker:20s}  {mac}  rssi={rssi}")
        dev = ble_seen[mac]
        dev["last_seen"] = ts
        if tracker and not dev.get("tracker_type"):
            dev["tracker_type"] = tracker
            ble_stats["trackers"] += 1
            print(f"\n[BLE TRACKER] {tracker:20s}  {mac}  rssi={rssi}")
        if name:
            dev["names"].add(name)

        if mac not in ble_live:
            ble_live[mac] = {
                "signal":            rssi,
                "last_heard":        ts,
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
                if ble_live[mac]["signal"] is None or rssi > ble_live[mac]["signal"]:
                    ble_live[mac]["signal"] = rssi
            ble_live[mac]["last_heard"] = ts
            if mfr_str and not ble_live[mac]["manufacturer_data"]:
                ble_live[mac]["manufacturer_data"] = mfr_str
            if svc_str:
                existing = ble_live[mac]["adv_services"] or ""
                new_svcs = set(existing.split(",")) | set(svc_str.split(","))
                new_svcs.discard("")
                ble_live[mac]["adv_services"] = ",".join(sorted(new_svcs)) or None
            if svc_data_str and not ble_live[mac]["adv_service_data"]:
                ble_live[mac]["adv_service_data"] = svc_data_str
            if tx_power is not None and ble_live[mac]["tx_power"] is None:
                ble_live[mac]["tx_power"] = tx_power
            if tracker and not ble_live[mac].get("tracker_type"):
                ble_live[mac]["tracker_type"] = tracker

        if name:
            ble_live[mac]["names"].add(name)


async def _ble_scan_async():
    """Run bleak BLE scanner indefinitely."""
    scanner = BleakScanner(
        detection_callback=on_ble_advertisement,
        adapter=BLE_IFACE,
        scanning_mode="active",
    )
    async with scanner:
        print(f"[BLE] Scanning on {BLE_IFACE} (active mode)")
        while True:
            await asyncio.sleep(3600)


def ble_scan_thread():
    """Run the async BLE scanner in its own event loop / thread."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_ble_scan_async())
    except Exception as e:
        print(f"\n[BLE ERROR] {e}")


# ---------------------------------------------------------------------------
# Snapshot thread
# ---------------------------------------------------------------------------

def snapshot_thread():
    time.sleep(next_boundary(SLOT_SECONDS))
    while True:
        ts           = now_utc()
        window_start = ts.timestamp() - SLOT_SECONDS

        with lock:
            # WiFi snapshot
            snap = {
                mac: dict(v) for mac, v in live.items()
                if v["last_heard"].timestamp() >= window_start
            }
            # Reset probe counts for next window
            for v in live.values():
                v["probe_count"] = 0

            # BLE snapshot
            ble_snap = {
                mac: dict(v, names=set(v.get("names", set())))
                for mac, v in ble_live.items()
                if v["last_heard"].timestamp() >= window_start
            }
            ble_live.clear()

        for mac, s in snap.items():
            pending_observations.append({
                "mac": mac, "type": s["type"],
                "interface": IFACE, "host": HOSTNAME,
                "signal": s["signal"], "channel": s.get("channel"),
                "freq_mhz": s.get("freq_mhz"), "channel_flags": s.get("channel_flags"),
                "ts": ts, "ssids": s.get("ssids", set()),
                "ht": s.get("ht", False), "vht": s.get("vht", False), "he": s.get("he", False),
                "vendor_ouis": s.get("vendor_ouis", set()),
                "oui": s.get("oui"), "is_randomized": s.get("is_randomized", False),
                "manufacturer": s.get("manufacturer"),
                "probe_count": s.get("probe_count", 1),
            })

        # BLE observations
        for mac, s in ble_snap.items():
            pending_observations.append({
                "mac": mac, "type": "BLE",
                "interface": BLE_IFACE, "host": HOSTNAME,
                "signal": s["signal"], "channel": 37,
                "freq_mhz": 2402, "channel_flags": None,
                "ts": ts, "ssids": s.get("names", set()),
                "ht": False, "vht": False, "he": False,
                "vendor_ouis": set(),
                "oui": s.get("oui"), "is_randomized": s.get("is_randomized", False),
                "manufacturer": s.get("manufacturer"),
                "probe_count": 1,
                "manufacturer_data": s.get("manufacturer_data"),
                "adv_services":      s.get("adv_services"),
                "adv_service_data":  s.get("adv_service_data"),
                "tx_power":          s.get("tx_power"),
                "tracker_type":      s.get("tracker_type"),
            })

        ble_active = len(ble_snap)
        ble_suffix = f"  BLE: {ble_stats['total']}({ble_active})" if BLE_ENABLED else ""
        print(
            f"\r[{ts.strftime('%H:%M:%S')} UTC]  "
            f"Band: {current_band['band']}GHz @ {current_band['freq']}MHz  |  "
            f"WiFi: {len(seen)}({len(snap)}){ble_suffix}  |  "
            f"Pending: {len(pending_observations)}   ",
            end="", flush=True
        )
        time.sleep(next_boundary(SLOT_SECONDS))


# ---------------------------------------------------------------------------
# Scanner self-registration & heartbeat
# ---------------------------------------------------------------------------

def register_scanner(conn, cur):
    """Register this scanner in the scanners table (upsert) and update heartbeat."""
    cur.execute("""
        INSERT INTO scanners (hostname, is_active, last_heartbeat)
        VALUES (%s, TRUE, %s)
        ON DUPLICATE KEY UPDATE
            is_active = TRUE,
            last_heartbeat = VALUES(last_heartbeat)
    """, (HOSTNAME, now_utc()))
    conn.commit()


# ---------------------------------------------------------------------------
# DB flush thread
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Local JSONL buffer — used when DB is unreachable
# ---------------------------------------------------------------------------

def obs_to_jsonl(obs):
    """Serialize an observation dict to a JSON-serializable dict."""
    return {
        "mac":           obs["mac"],
        "type":          obs["type"],
        "interface":     obs["interface"],
        "host":          obs["host"],
        "signal":        obs["signal"],
        "channel":       obs["channel"],
        "freq_mhz":      obs.get("freq_mhz"),
        "channel_flags": obs.get("channel_flags"),
        "ts":            obs["ts"].isoformat(),
        "ssids":         list(obs.get("ssids", set())),
        "ht":            obs.get("ht", False),
        "vht":           obs.get("vht", False),
        "he":            obs.get("he", False),
        "vendor_ouis":   list(obs.get("vendor_ouis", set())),
        "oui":           obs.get("oui"),
        "is_randomized": obs.get("is_randomized", False),
        "manufacturer":  obs.get("manufacturer"),
        "probe_count":   obs.get("probe_count", 1),
        "manufacturer_data": obs.get("manufacturer_data"),
        "adv_services":      obs.get("adv_services"),
        "adv_service_data":  obs.get("adv_service_data"),
        "tx_power":          obs.get("tx_power"),
        "tracker_type":      obs.get("tracker_type"),
    }


def obs_from_jsonl(record):
    """Deserialize a JSONL record back to an observation dict."""
    record["ts"]          = datetime.fromisoformat(record["ts"])
    record["ssids"]       = set(record["ssids"])
    record["vendor_ouis"] = set(record["vendor_ouis"])
    return record


def write_to_buffer(batch):
    """Append a batch of observations to the local JSONL buffer file."""
    try:
        with open(BUFFER_FILE, 'a') as f:
            for obs in batch:
                f.write(json.dumps(obs_to_jsonl(obs)) + '\n')
        print(f"\n[BUFFER] Wrote {len(batch)} observations to local buffer ({BUFFER_FILE})")
    except Exception as e:
        print(f"\n[BUFFER ERROR] Could not write to local buffer: {e}")


def read_and_clear_buffer():
    """Read all buffered observations from disk and clear the file. Returns list."""
    if not os.path.exists(BUFFER_FILE) or os.path.getsize(BUFFER_FILE) == 0:
        return []
    records = []
    try:
        with open(BUFFER_FILE, 'r') as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(obs_from_jsonl(json.loads(line)))
                    except (json.JSONDecodeError, KeyError):
                        pass
        open(BUFFER_FILE, 'w').close()  # clear after reading
        if records:
            print(f"\n[BUFFER] Replaying {len(records)} buffered observations from disk")
    except Exception as e:
        print(f"\n[BUFFER ERROR] Could not read local buffer: {e}")
    return records


def flush_to_db():
    while True:
        time.sleep(FLUSH_INTERVAL)
        # Replay any locally buffered observations first
        buffered = read_and_clear_buffer()

        with lock:
            batch = buffered + pending_observations.copy()
            pending_observations.clear()

        try:
            conn   = mysql.connector.connect(**DB)
            cur    = conn.cursor()
            ts_now = now_utc()

            # Always update heartbeat so scanner shows online even with no traffic
            register_scanner(conn, cur)

            if not batch:
                conn.commit()
                conn.close()
                continue

            device_rows = {}
            for obs in batch:
                mac = obs["mac"]
                if mac not in device_rows:
                    device_rows[mac] = {
                        "type": obs["type"], "first_seen": obs["ts"], "last_seen": obs["ts"],
                        "ssids": set(), "oui": obs.get("oui"),
                        "manufacturer": obs.get("manufacturer"),
                        "is_randomized": obs.get("is_randomized", False),
                        "ht": False, "vht": False, "he": False, "vendor_ouis": set(),
                    }
                d = device_rows[mac]
                d["last_seen"] = max(d["last_seen"], obs["ts"])
                d["ssids"].update(obs.get("ssids", set()))
                d["vendor_ouis"].update(obs.get("vendor_ouis", set()))
                if obs.get("ht"):  d["ht"]  = True
                if obs.get("vht"): d["vht"] = True
                if obs.get("he"):  d["he"]  = True

            for mac, d in device_rows.items():
                cur.execute("""
                    INSERT INTO devices
                        (mac, device_type, oui, manufacturer, is_randomized,
                         ht_capable, vht_capable, he_capable, first_seen, last_seen)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        last_seen    = GREATEST(last_seen,   VALUES(last_seen)),
                        manufacturer = COALESCE(manufacturer, VALUES(manufacturer)),
                        ht_capable   = GREATEST(ht_capable,  VALUES(ht_capable)),
                        vht_capable  = GREATEST(vht_capable, VALUES(vht_capable)),
                        he_capable   = GREATEST(he_capable,  VALUES(he_capable))
                """, (mac, d["type"], d["oui"], d["manufacturer"], int(d["is_randomized"]),
                      int(d["ht"]), int(d["vht"]), int(d["he"]), d["first_seen"], d["last_seen"]))

                for ssid in d["ssids"]:
                    if is_valid_ssid(ssid):
                        cur.execute("""
                            INSERT IGNORE INTO ssids (mac, ssid, first_seen) VALUES (%s, %s, %s)
                        """, (mac, ssid, d["first_seen"]))

                for voui in d["vendor_ouis"]:
                    cur.execute("""
                        INSERT IGNORE INTO vendor_ies (mac, vendor_oui, first_seen) VALUES (%s, %s, %s)
                    """, (mac, voui, d["first_seen"]))

            cur.executemany("""
                INSERT INTO observations
                    (mac, interface, scanner_host, signal_dbm, channel,
                     freq_mhz, channel_flags, probe_count,
                     manufacturer_data, adv_services, adv_service_data,
                     tx_power, tracker_type, recorded_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, [(o["mac"], o["interface"], o["host"], o["signal"], o["channel"],
                   o.get("freq_mhz"), o.get("channel_flags"), o.get("probe_count", 1),
                   o.get("manufacturer_data"), o.get("adv_services"),
                   o.get("adv_service_data"), o.get("tx_power"),
                   o.get("tracker_type"), o["ts"]) for o in batch])

            conn.commit()
            cur.close()
            conn.close()
            print(f"\n[DB] Wrote {len(batch)} snapshots at {ts_now.strftime('%H:%M:%S')} UTC")

        except mysql.connector.Error as e:
            print(f"\n[DB ERROR] {e} — writing to local buffer")
            write_to_buffer(batch)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def on_exit(sig, frame):
    ble_msg = f", {ble_stats['total']} BLE ({ble_stats['trackers']} trackers)" if BLE_ENABLED else ""
    print(f"\n\nShutting down. {len(seen)} WiFi devices{ble_msg}.")
    sys.exit(0)


if __name__ == "__main__":
    signal.signal(signal.SIGINT, on_exit)

    bands          = detect_supported_bands(IFACE)
    slots_per_band = (CYCLE_SECONDS // SLOT_SECONDS) // len(bands)
    schedule       = build_schedule(bands)

    print(f"Scanner : {HOSTNAME}/{IFACE}")
    print(f"Bands   : {', '.join(b + 'GHz' for b in bands)}  ({slots_per_band * SLOT_SECONDS}s per band)")
    print(f"Schedule: {schedule}")
    print(f"BLE     : {'ON (' + BLE_IFACE + ')' if BLE_ENABLED else 'OFF (pip install bleak to enable)'}")
    print(f"Flush   : every {FLUSH_INTERVAL}s | All times UTC")
    print("Ctrl+C to stop\n")

    band0, freq0 = get_target_freq(time.time(), schedule, slots_per_band)
    set_freq(IFACE, freq0)
    current_band["band"] = band0
    current_band["freq"] = freq0

    threading.Thread(target=channel_hopper,  args=(schedule, slots_per_band), daemon=True).start()
    threading.Thread(target=snapshot_thread, daemon=True).start()
    threading.Thread(target=flush_to_db,     daemon=True).start()
    if BLE_ENABLED:
        threading.Thread(target=ble_scan_thread, daemon=True).start()

    sniff(iface=IFACE, prn=handle_packet, store=False)
