#!/usr/bin/env python3
"""
Sync known devices from port_scan database into air_scan's known_devices table.

Reads MAC addresses from port_scan.hosts and port_scan.host_network_ids,
matches them against wireless.devices, and populates wireless.known_devices.

Can run as a one-shot or on an interval.

Usage:
    python3 sync_known_devices.py [--interval 300] [--once]
"""

import argparse
import os
import sys
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


# ---------------------------------------------------------------------------
# Config — two separate DB connections
# ---------------------------------------------------------------------------

WIRELESS_DB = {
    "host":     os.environ.get("DB_HOST", "localhost"),
    "user":     os.environ["DB_USER"],
    "password": os.environ["DB_PASS"],
    "database": os.environ.get("DB_NAME", "wireless"),
}

# Parse port_scan DATABASE_URL: mysql+pymysql://user:pass@host:port/dbname
def parse_db_url(url):
    """Parse SQLAlchemy-style DATABASE_URL into connection dict."""
    # Strip scheme
    url = url.split("://", 1)[1]
    userpass, rest = url.split("@", 1)
    user, password = userpass.split(":", 1)
    hostport, dbname = rest.split("/", 1)
    host, port = hostport.split(":", 1) if ":" in hostport else (hostport, "3306")
    return {"host": host, "port": int(port), "user": user, "password": password, "database": dbname}


PORT_SCAN_URL = os.environ.get("PORT_SCAN_DATABASE_URL", "")
if not PORT_SCAN_URL:
    # Try reading from port_scan .env directly
    _ps_env = Path(__file__).resolve().parent.parent.parent / "port_scan" / ".env"
    if _ps_env.exists():
        with open(_ps_env) as f:
            for line in f:
                line = line.strip()
                if line.startswith("DATABASE_URL="):
                    PORT_SCAN_URL = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break

if not PORT_SCAN_URL:
    print("[ERROR] No port_scan DATABASE_URL found. Set PORT_SCAN_DATABASE_URL in .env or ensure ../port_scan/.env exists.")
    sys.exit(1)

PORT_SCAN_DB = parse_db_url(PORT_SCAN_URL)


# ---------------------------------------------------------------------------
# Sync logic
# ---------------------------------------------------------------------------

def sync():
    """Pull MACs from port_scan, match against wireless.devices, update known_devices."""
    ts = datetime.now(timezone.utc).replace(tzinfo=None)

    # 1. Read all MAC addresses from port_scan
    ps_conn = mysql.connector.connect(**PORT_SCAN_DB)
    ps_cur = ps_conn.cursor(dictionary=True)

    # Get current_mac from hosts + all historical MACs from host_network_ids
    ps_cur.execute("""
        SELECT DISTINCT LOWER(mac) AS mac, host_id, hostname, label_source FROM (
            SELECT h.current_mac AS mac, h.id AS host_id, h.hostname, 'hosts.current_mac' AS label_source
            FROM hosts h
            WHERE h.current_mac IS NOT NULL AND h.current_mac != ''

            UNION

            SELECT hni.mac_address AS mac, hni.host_id, h.hostname, 'host_network_ids' AS label_source
            FROM host_network_ids hni
            JOIN hosts h ON h.id = hni.host_id
            WHERE hni.mac_address IS NOT NULL AND hni.mac_address != ''
        ) combined
    """)
    port_scan_macs = ps_cur.fetchall()
    ps_cur.close()
    ps_conn.close()

    if not port_scan_macs:
        print(f"  No MACs found in port_scan")
        return 0

    # Deduplicate: keep one entry per MAC, prefer hosts.current_mac source
    mac_map = {}
    for row in port_scan_macs:
        mac = row["mac"]
        if mac not in mac_map or row["label_source"] == "hosts.current_mac":
            mac_map[mac] = row

    print(f"  port_scan: {len(mac_map)} unique MACs")

    # 2. Read known wireless device MACs
    w_conn = mysql.connector.connect(**WIRELESS_DB)
    w_cur = w_conn.cursor(dictionary=True)

    w_cur.execute("SELECT mac FROM devices")
    wireless_macs = {row["mac"] for row in w_cur.fetchall()}
    print(f"  wireless:  {len(wireless_macs)} devices")

    # 3. Upsert into known_devices
    matched = 0
    unmatched_known = 0

    for mac, info in mac_map.items():
        hostname = info["hostname"] or ""
        host_id = info["host_id"]

        # Upsert — mark as 'known' if MAC exists in wireless devices,
        # still insert if not (so we have the cross-reference ready when it appears)
        in_wireless = mac in wireless_macs
        status = "known" if in_wireless else "unknown"

        w_cur.execute("""
            INSERT INTO known_devices (mac, port_scan_host_id, label, status, synced_at)
            VALUES (%s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                port_scan_host_id = VALUES(port_scan_host_id),
                label = VALUES(label),
                status = CASE
                    WHEN status IN ('guest', 'rogue') THEN status
                    ELSE VALUES(status)
                END,
                synced_at = VALUES(synced_at)
        """, (mac, host_id, hostname, status, ts))

        if in_wireless:
            matched += 1
        else:
            unmatched_known += 1

    w_conn.commit()

    # 4. Check for wireless devices NOT in port_scan (truly unknown)
    w_cur.execute("""
        SELECT d.mac FROM devices d
        LEFT JOIN known_devices kd ON d.mac = kd.mac
        WHERE kd.mac IS NULL
    """)
    unknown_macs = [row["mac"] for row in w_cur.fetchall()]

    for mac in unknown_macs:
        w_cur.execute("""
            INSERT INTO known_devices (mac, status, synced_at)
            VALUES (%s, 'unknown', %s)
            ON DUPLICATE KEY UPDATE synced_at = VALUES(synced_at)
        """, (mac, ts))

    w_conn.commit()
    w_cur.close()
    w_conn.close()

    print(f"  Matched: {matched} | Port-scan only: {unmatched_known} | Unknown wireless: {len(unknown_macs)}")
    return matched


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Sync known devices from port_scan to air_scan")
    parser.add_argument("--interval", type=int, default=300, help="Sync interval in seconds (default: 300)")
    parser.add_argument("--once", action="store_true", help="Sync once and exit")
    args = parser.parse_args()

    print("=== Known Device Sync ===")
    print(f"port_scan DB: {PORT_SCAN_DB['host']}/{PORT_SCAN_DB['database']}")
    print(f"wireless  DB: {WIRELESS_DB['host']}/{WIRELESS_DB['database']}")
    print(f"Interval: {args.interval}s")
    print()

    if args.once:
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        print(f"[{ts} UTC] Syncing...")
        sync()
        return

    while True:
        try:
            ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
            print(f"[{ts} UTC] Syncing...")
            sync()
        except KeyboardInterrupt:
            print("\nStopped.")
            break
        except Exception as e:
            print(f"[ERROR] {e}")

        time.sleep(args.interval)


if __name__ == "__main__":
    main()
