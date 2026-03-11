#!/usr/bin/env python3
"""
Pull-based scanner client — fetches scan results from an OpenWrt router
running router_capture.sh. Handles two data types:
  - JSONL files from 'iw scan' (APs only)
  - pcap files from tcpdump monitor mode (APs + Clients)

Parses both with scapy and inserts into the same MySQL database as wifi_scanner.py.

Usage:
    python3 pull_scanner.py [--interval 60] [--router 192.168.1.3]

Env vars: DB_HOST, DB_USER, DB_PASS, DB_NAME, DB_SSL
          ROUTER_HOST, ROUTER_USER, ROUTER_PASS
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
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

import mysql.connector
from scapy.all import (
    rdpcap, Dot11, Dot11Beacon, Dot11ProbeReq, Dot11Elt, RadioTap, conf
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DB = {
    "host":         os.environ.get("DB_HOST", "localhost"),
    "user":         os.environ["DB_USER"],
    "password":     os.environ["DB_PASS"],
    "database":     os.environ.get("DB_NAME", "wireless"),
}

ROUTER_HOST = os.environ.get("ROUTER_HOST", "192.168.1.3")
ROUTER_USER = os.environ.get("ROUTER_USER", "root")
ROUTER_PASS = os.environ.get("ROUTER_PASS", "")
REMOTE_DIR  = "/tmp/scans"
SCANNER_HOST_LABEL = "openwrt-wrt1900ac"
BUFFER_FILE = os.path.expanduser("~/scanner/openwrt_offline_buffer.jsonl")

# Filter out the router's own MACs from monitor captures
IGNORE_MACS = {
    "00:25:9c:13:20:e8",  # wlan0 (built-in 2.4GHz)
    "00:25:9c:13:20:e9",  # wlan1 (built-in 5GHz)
}

# ---------------------------------------------------------------------------
# SSH helpers
# ---------------------------------------------------------------------------

def _ssh_cmd(cmd):
    ssh = ["sshpass", "-p", ROUTER_PASS,
           "ssh", "-o", "StrictHostKeyChecking=no",
           f"{ROUTER_USER}@{ROUTER_HOST}", cmd]
    r = subprocess.run(ssh, capture_output=True, text=True, timeout=30)
    return r.stdout.strip(), r.returncode == 0


def _ssh_rm(remote_path):
    _ssh_cmd(f"rm -f {remote_path}")


def _scp_get(remote_path, local_path):
    """Download a file from the router using legacy SCP protocol."""
    scp = ["sshpass", "-p", ROUTER_PASS,
           "scp", "-O", "-o", "StrictHostKeyChecking=no",
           f"{ROUTER_USER}@{ROUTER_HOST}:{remote_path}", local_path]
    r = subprocess.run(scp, capture_output=True, text=True, timeout=60)
    return r.returncode == 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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

def freq_to_flags(freq):
    """Derive channel flags from frequency when RadioTap doesn't provide them."""
    if not freq:
        return None
    freq = int(freq)
    if 2412 <= freq <= 2484:
        return "CCK+2GHz"
    elif 5000 <= freq <= 5900:
        return "OFDM+5GHz"
    elif 5900 < freq <= 7300:
        return "OFDM+6GHz"
    return None

def freq_to_channel(freq):
    """Convert frequency MHz to channel number."""
    if not freq:
        return None
    freq = int(freq)
    if 2412 <= freq <= 2484:
        if freq == 2484:
            return 14
        return (freq - 2407) // 5
    elif 5180 <= freq <= 5825:
        return (freq - 5000) // 5
    elif 5955 <= freq <= 7115:
        return (freq - 5950) // 5
    return None


# ---------------------------------------------------------------------------
# Parse JSONL scan results from router
# ---------------------------------------------------------------------------

def parse_scan_files(file_contents_list):
    """Parse JSONL scan data into observation dicts."""
    observations = []

    for lines in file_contents_list:
        for line in lines.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue

            mac = r.get("mac", "").lower()
            if not mac or mac == "ff:ff:ff:ff:ff:ff":
                continue

            freq = int(r.get("freq", 0)) if r.get("freq") else None
            try:
                ts = datetime.fromisoformat(r["ts"].replace("Z", "+00:00")).replace(tzinfo=None)
            except:
                ts = datetime.now(timezone.utc).replace(tzinfo=None)

            observations.append({
                "mac": mac,
                "type": "AP",  # iw scan only sees APs (beacons/probe responses)
                "interface": r.get("iface", "wlan0"),
                "host": SCANNER_HOST_LABEL,
                "signal": float(r.get("signal", 0)) if r.get("signal") else None,
                "channel": freq_to_channel(freq),
                "freq_mhz": freq,
                "channel_flags": freq_to_flags(freq),
                "ts": ts,
                "ssids": {r["ssid"]} if r.get("ssid") else set(),
                "ht": bool(r.get("ht", False)),
                "vht": bool(r.get("vht", False)),
                "he": bool(r.get("he", False)),
                "vendor_ouis": set(),
                "oui": get_oui(mac),
                "is_randomized": mac_is_randomized(mac),
                "manufacturer": get_manufacturer(mac),
            })

    return observations


def parse_pcap_file(filepath):
    """Parse a pcap file from monitor mode capture into observation dicts."""
    observations = []
    try:
        packets = rdpcap(str(filepath))
    except Exception as e:
        print(f"  [WARN] Could not read {filepath}: {e}")
        return []

    for pkt in packets:
        if not pkt.haslayer(Dot11):
            continue

        mac = device_type = ssid = channel = None
        sig = None
        freq, flags = None, None
        ht = vht = he = False
        vendor_ouis = set()

        try:    sig = pkt[RadioTap].dBm_AntSignal
        except: pass
        try:    freq = int(pkt[RadioTap].ChannelFrequency)
        except: pass
        try:    flags = str(pkt[RadioTap].ChannelFlags)
        except: pass

        if pkt.haslayer(Dot11Beacon):
            mac = pkt[Dot11].addr3
            device_type = "AP"
            ssid = pkt[Dot11Elt].info.decode(errors="replace") if pkt.haslayer(Dot11Elt) else ""
            # Get channel from DS Parameter Set (IE 3)
            elt = pkt[Dot11Elt] if pkt.haslayer(Dot11Elt) else None
            while elt and isinstance(elt, Dot11Elt):
                if elt.ID == 3:
                    channel = elt.info[0] if isinstance(elt.info, bytes) else ord(elt.info)
                    break
                elt = elt.payload if isinstance(getattr(elt, "payload", None), Dot11Elt) else None
        elif pkt.haslayer(Dot11ProbeReq):
            mac = pkt[Dot11].addr2
            device_type = "Client"
            ssid = pkt[Dot11Elt].info.decode(errors="replace") if pkt.haslayer(Dot11Elt) else ""

        if not mac or mac == "ff:ff:ff:ff:ff:ff" or mac in IGNORE_MACS:
            continue

        # Parse HT/VHT/HE capabilities and vendor IEs
        elt = pkt[Dot11Elt] if pkt.haslayer(Dot11Elt) else None
        while elt and isinstance(elt, Dot11Elt):
            if   elt.ID == 45:  ht  = True
            elif elt.ID == 191: vht = True
            elif elt.ID == 255 and elt.info and elt.info[0] == 35: he = True
            elif elt.ID == 221 and elt.info and len(elt.info) >= 3:
                vendor_ouis.add(":".join(f"{b:02x}" for b in elt.info[:3]))
            elt = elt.payload if isinstance(getattr(elt, "payload", None), Dot11Elt) else None

        try:
            ts = datetime.fromtimestamp(float(pkt.time), tz=timezone.utc).replace(tzinfo=None)
        except:
            ts = datetime.now(timezone.utc).replace(tzinfo=None)

        if channel is None:
            channel = freq_to_channel(freq)

        observations.append({
            "mac": mac, "type": device_type,
            "interface": "wlan2mon",
            "host": SCANNER_HOST_LABEL,
            "signal": sig, "channel": channel,
            "freq_mhz": freq, "channel_flags": flags or freq_to_flags(freq),
            "ts": ts, "ssids": {ssid} if ssid else set(),
            "ht": ht, "vht": vht, "he": he,
            "vendor_ouis": vendor_ouis,
            "oui": get_oui(mac),
            "is_randomized": mac_is_randomized(mac),
            "manufacturer": get_manufacturer(mac),
        })

    return observations


SLOT_SECONDS = 10

def align_ts(ts):
    """Floor a naive-UTC datetime to the nearest 10-second boundary."""
    epoch = int(ts.replace(tzinfo=timezone.utc).timestamp())
    aligned = epoch - (epoch % SLOT_SECONDS)
    return datetime.fromtimestamp(aligned, tz=timezone.utc).replace(tzinfo=None)


def dedup_observations(observations):
    """Keep one observation per MAC per 10s slot, merging SSIDs/caps.

    Aligns all timestamps to 10-second boundaries so observations can be
    compared across scanners (matches wifi_scanner.py snapshot behavior).
    """
    # Key: (mac, aligned_ts)
    best = {}
    for obs in observations:
        mac = obs["mac"]
        slot_ts = align_ts(obs["ts"])
        key = (mac, slot_ts)

        if key not in best:
            obs["ts"] = slot_ts
            best[key] = obs
        else:
            existing = best[key]
            # Keep the most recent signal reading within the slot
            if obs["ts"] > existing["ts"]:
                existing["signal"] = obs["signal"]
            existing["ssids"] |= obs["ssids"]
            existing["vendor_ouis"] |= obs.get("vendor_ouis", set())
            if obs["ht"]:  existing["ht"]  = True
            if obs["vht"]: existing["vht"] = True
            if obs["he"]:  existing["he"]  = True

    return list(best.values())


# ---------------------------------------------------------------------------
# Scanner self-registration & heartbeat
# ---------------------------------------------------------------------------

def register_scanner(conn, cur, hostname):
    """Register a scanner in the scanners table (upsert) and update heartbeat."""
    ts = datetime.now(timezone.utc).replace(tzinfo=None)
    cur.execute("""
        INSERT INTO scanners (hostname, is_active, last_heartbeat)
        VALUES (%s, TRUE, %s)
        ON DUPLICATE KEY UPDATE
            is_active = TRUE,
            last_heartbeat = VALUES(last_heartbeat)
    """, (hostname, ts))
    conn.commit()


# ---------------------------------------------------------------------------
# DB flush (same schema as wifi_scanner.py)
# ---------------------------------------------------------------------------

def obs_to_jsonl(obs):
    return {
        "mac": obs["mac"], "type": obs["type"],
        "interface": obs["interface"], "host": obs["host"],
        "signal": obs["signal"], "channel": obs["channel"],
        "freq_mhz": obs.get("freq_mhz"), "channel_flags": obs.get("channel_flags"),
        "ts": obs["ts"].isoformat(),
        "ssids": list(obs.get("ssids", set())),
        "ht": obs.get("ht", False), "vht": obs.get("vht", False),
        "he": obs.get("he", False),
        "vendor_ouis": list(obs.get("vendor_ouis", set())),
        "oui": obs.get("oui"), "is_randomized": obs.get("is_randomized", False),
        "manufacturer": obs.get("manufacturer"),
    }


def write_to_buffer(batch):
    try:
        os.makedirs(os.path.dirname(BUFFER_FILE), exist_ok=True)
        with open(BUFFER_FILE, 'a') as f:
            for obs in batch:
                f.write(json.dumps(obs_to_jsonl(obs)) + '\n')
        print(f"  [BUFFER] Wrote {len(batch)} observations to {BUFFER_FILE}")
    except Exception as e:
        print(f"  [BUFFER ERROR] {e}")


def read_and_clear_buffer():
    if not os.path.exists(BUFFER_FILE) or os.path.getsize(BUFFER_FILE) == 0:
        return []
    records = []
    try:
        with open(BUFFER_FILE, 'r') as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        r = json.loads(line)
                        r["ts"] = datetime.fromisoformat(r["ts"])
                        r["ssids"] = set(r["ssids"])
                        r["vendor_ouis"] = set(r["vendor_ouis"])
                        records.append(r)
                    except (json.JSONDecodeError, KeyError):
                        pass
        open(BUFFER_FILE, 'w').close()
        if records:
            print(f"  [BUFFER] Replaying {len(records)} buffered observations")
    except Exception as e:
        print(f"  [BUFFER ERROR] {e}")
    return records


def flush_to_db(observations):
    buffered = read_and_clear_buffer()
    batch = buffered + observations

    if not batch:
        return

    try:
        conn = mysql.connector.connect(**DB)
        cur  = conn.cursor()

        # Update scanner heartbeat for the router
        register_scanner(conn, cur, SCANNER_HOST_LABEL)

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
                    last_seen   = GREATEST(last_seen,   VALUES(last_seen)),
                    ht_capable  = GREATEST(ht_capable,  VALUES(ht_capable)),
                    vht_capable = GREATEST(vht_capable, VALUES(vht_capable)),
                    he_capable  = GREATEST(he_capable,  VALUES(he_capable))
            """, (mac, d["type"], d["oui"], d["manufacturer"], int(d["is_randomized"]),
                  int(d["ht"]), int(d["vht"]), int(d["he"]), d["first_seen"], d["last_seen"]))

            for ssid in d["ssids"]:
                if ssid:
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
                 freq_mhz, channel_flags, recorded_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, [(o["mac"], o["interface"], o["host"], o["signal"], o["channel"],
               o.get("freq_mhz"), o.get("channel_flags"), o["ts"]) for o in batch])

        conn.commit()
        cur.close()
        conn.close()
        print(f"  [DB] Wrote {len(batch)} observations ({len(device_rows)} devices)")

    except mysql.connector.Error as e:
        print(f"  [DB ERROR] {e} — buffering locally")
        write_to_buffer(batch)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def pull_and_process():
    """Pull completed scan + pcap files from router, parse, flush to DB."""
    stdout, ok = _ssh_cmd(f"ls -1 {REMOTE_DIR}/ 2>/dev/null")
    if not ok or not stdout:
        return 0

    all_files = [f"{REMOTE_DIR}/{f}" for f in stdout.splitlines() if f.endswith((".jsonl", ".pcap"))]
    if not all_files:
        return 0

    jsonl_files = sorted([f for f in all_files if f.endswith(".jsonl")])
    pcap_files  = sorted([f for f in all_files if f.endswith(".pcap")])

    # Skip the most recent of each type (might still be written to)
    to_pull_jsonl = jsonl_files[:-1] if jsonl_files else []
    to_pull_pcap  = pcap_files[:-1] if pcap_files else []

    if not to_pull_jsonl and not to_pull_pcap:
        return 0

    all_observations = []

    # Process JSONL files (iw scan — APs)
    if to_pull_jsonl:
        print(f"  JSONL: {len(to_pull_jsonl)} files")
        file_contents = []
        for remote_path in to_pull_jsonl:
            stdout, ok = _ssh_cmd(f"cat {remote_path}")
            if ok and stdout:
                file_contents.append(stdout)
                _ssh_rm(remote_path)
        all_observations.extend(parse_scan_files(file_contents))

    # Process pcap files (monitor mode — APs + Clients)
    if to_pull_pcap:
        print(f"  pcap:  {len(to_pull_pcap)} files")
        with tempfile.TemporaryDirectory() as tmpdir:
            for remote_path in to_pull_pcap:
                local_path = os.path.join(tmpdir, os.path.basename(remote_path))
                if _scp_get(remote_path, local_path):
                    obs = parse_pcap_file(local_path)
                    all_observations.extend(obs)
                    _ssh_rm(remote_path)
                else:
                    print(f"  [WARN] Failed to download {remote_path}")

    if not all_observations:
        print("  No observations parsed")
        return 0

    deduped = dedup_observations(all_observations)
    n_ap = sum(1 for o in deduped if o["type"] == "AP")
    n_client = sum(1 for o in deduped if o["type"] == "Client")
    print(f"  {len(all_observations)} raw -> {len(deduped)} unique ({n_ap} APs, {n_client} Clients)")
    flush_to_db(deduped)
    return len(deduped)


def main():
    parser = argparse.ArgumentParser(description="Pull scan results from OpenWrt router")
    parser.add_argument("--interval", type=int, default=60, help="Pull interval in seconds (default: 60)")
    parser.add_argument("--router", type=str, default=None, help="Router IP")
    parser.add_argument("--once", action="store_true", help="Pull once and exit")
    args = parser.parse_args()

    global ROUTER_HOST
    if args.router:
        ROUTER_HOST = args.router

    print(f"=== Pull Scanner ===")
    print(f"Router  : {ROUTER_USER}@{ROUTER_HOST}:{REMOTE_DIR}")
    print(f"DB      : {DB['host']}/{DB['database']}")
    print(f"Interval: {args.interval}s")
    print()

    _, ok = _ssh_cmd("echo ok")
    if not ok:
        print("[ERROR] Cannot reach router via SSH.")
        sys.exit(1)

    if args.once:
        pull_and_process()
        return

    while True:
        try:
            ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
            print(f"[{ts} UTC] Pulling...")
            pull_and_process()
        except KeyboardInterrupt:
            print("\nStopped.")
            break
        except Exception as e:
            print(f"[ERROR] {e}")

        time.sleep(args.interval)


if __name__ == "__main__":
    main()
