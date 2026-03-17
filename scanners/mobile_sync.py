#!/usr/bin/env python3
"""
Mobile Scanner Sync
Pushes locally stored SQLite scan data to the central MySQL database.

Run this when the Pi is connected to ethernet (or any network with DB access).
Finds SQLite DB on USB drive (or --db PATH), syncs unsynced observations,
marks them synced so re-runs are safe.

Usage:
    python3 mobile_sync.py                    # auto-detect DB on USB drive
    python3 mobile_sync.py --db /path/to/mobile_scan.db
    python3 mobile_sync.py --dry-run          # show what would be synced
"""

import sys
import os
import sqlite3
import argparse
import subprocess
from datetime import datetime
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

import mysql.connector

# ---------------------------------------------------------------------------
# Args
# ---------------------------------------------------------------------------

parser = argparse.ArgumentParser(description="Sync mobile scan SQLite to central MySQL")
parser.add_argument("--db",      default=None, help="Path to mobile_scan.db (auto-detect if omitted)")
parser.add_argument("--dry-run", action="store_true", help="Show what would be synced without writing")
args = parser.parse_args()

MYSQL_CFG = {
    "host":         os.environ.get("DB_HOST", "localhost"),
    "user":         os.environ["DB_USER"],
    "password":     os.environ["DB_PASS"],
    "database":     os.environ.get("DB_NAME", "wireless"),
    "ssl_disabled": os.environ.get("DB_SSL", "disabled").lower() == "disabled",
}

# ---------------------------------------------------------------------------
# Find SQLite DB
# ---------------------------------------------------------------------------

def find_sqlite_db():
    """Search USB drive mount points for mobile_scan.db."""
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
        for candidate in sorted(base.rglob("mobile_scan.db")):
            return candidate

    # Fallback: /tmp
    p = Path("/tmp/air_scan/mobile_scan.db")
    if p.exists():
        return p

    return None


# ---------------------------------------------------------------------------
# Sync logic
# ---------------------------------------------------------------------------

def sync(sqlite_path, dry_run=False):
    print(f"Source : {sqlite_path}")

    src = sqlite3.connect(str(sqlite_path))
    src.row_factory = sqlite3.Row

    # Check for unsynced data
    unsynced_count = src.execute(
        "SELECT COUNT(*) FROM observations WHERE synced = 0"
    ).fetchone()[0]

    if unsynced_count == 0:
        print("Nothing to sync — all observations already synced.")
        src.close()
        return

    print(f"Unsynced observations: {unsynced_count}")

    if dry_run:
        sessions = src.execute("""
            SELECT s.id, s.scanner_host, s.scan_iface, s.started_at, s.ended_at,
                   COUNT(o.id) as obs_count
            FROM sessions s
            JOIN observations o ON o.session_id = s.id
            WHERE o.synced = 0
            GROUP BY s.id
        """).fetchall()
        print("\nSessions to sync:")
        for s in sessions:
            print(f"  Session {s['id']}: {s['scanner_host']}/{s['scan_iface']}"
                  f"  started={s['started_at']}  obs={s['obs_count']}")
        src.close()
        return

    # Connect to MySQL
    try:
        dst = mysql.connector.connect(**MYSQL_CFG)
        cur = dst.cursor()
        print(f"Connected to MySQL: {MYSQL_CFG['host']}/{MYSQL_CFG['database']}")
    except mysql.connector.Error as e:
        print(f"[ERROR] Cannot connect to MySQL: {e}")
        src.close()
        sys.exit(1)

    # Pull unsynced observations with their device data
    # session_id stored as "hostname:started_at" for traceability
    rows = src.execute("""
        SELECT
            o.id        AS obs_id,
            (s.scanner_host || ':' || s.started_at) AS session_id,
            o.mac,
            o.interface,
            o.scanner_host,
            o.signal_dbm,
            o.channel,
            o.freq_mhz,
            o.channel_flags,
            o.gps_lat,
            o.gps_lon,
            o.gps_fix,
            o.recorded_at,
            d.device_type,
            d.oui,
            d.manufacturer,
            d.is_randomized,
            d.ht_capable,
            d.vht_capable,
            d.he_capable,
            d.first_seen,
            d.last_seen
        FROM observations o
        JOIN devices d ON d.mac = o.mac
        JOIN sessions s ON s.id = o.session_id
        WHERE o.synced = 0
        ORDER BY o.recorded_at
    """).fetchall()

    # Pull SSIDs and vendor IEs for affected MACs
    macs = list({r["mac"] for r in rows})
    placeholders = ",".join("?" * len(macs))

    ssid_rows = src.execute(
        f"SELECT mac, ssid, first_seen FROM ssids WHERE mac IN ({placeholders})",
        macs
    ).fetchall() if macs else []

    vie_rows = src.execute(
        f"SELECT mac, vendor_oui, first_seen FROM vendor_ies WHERE mac IN ({placeholders})",
        macs
    ).fetchall() if macs else []

    ssids_by_mac   = {}
    for r in ssid_rows:
        ssids_by_mac.setdefault(r["mac"], []).append((r["ssid"], r["first_seen"]))

    vies_by_mac    = {}
    for r in vie_rows:
        vies_by_mac.setdefault(r["mac"], []).append((r["vendor_oui"], r["first_seen"]))

    # Also ensure scanner is registered in central DB
    synced_hosts = set()
    for row in rows:
        host = row["scanner_host"]
        if host not in synced_hosts:
            cur.execute("""
                INSERT INTO scanners (hostname, label, is_active, last_heartbeat)
                VALUES (%s, %s, FALSE, %s)
                ON DUPLICATE KEY UPDATE last_heartbeat = VALUES(last_heartbeat)
            """, (host, f"{host} (mobile)", row["recorded_at"]))
            synced_hosts.add(host)

    # Upsert devices
    seen_macs = set()
    for row in rows:
        mac = row["mac"]
        if mac in seen_macs:
            continue
        seen_macs.add(mac)

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
        """, (
            mac, row["device_type"], row["oui"], row["manufacturer"],
            row["is_randomized"], row["ht_capable"], row["vht_capable"], row["he_capable"],
            row["first_seen"], row["last_seen"],
        ))

        for ssid, first_seen in ssids_by_mac.get(mac, []):
            if ssid:
                cur.execute(
                    "INSERT IGNORE INTO ssids (mac, ssid, first_seen) VALUES (%s, %s, %s)",
                    (mac, ssid, first_seen)
                )

        for voui, first_seen in vies_by_mac.get(mac, []):
            cur.execute(
                "INSERT IGNORE INTO vendor_ies (mac, vendor_oui, first_seen) VALUES (%s, %s, %s)",
                (mac, voui, first_seen)
            )

    # Insert observations in batches
    BATCH_SIZE  = 500
    obs_synced  = []
    total_written = 0

    obs_batch = []
    for row in rows:
        obs_batch.append((
            row["mac"], row["interface"], row["scanner_host"],
            row["signal_dbm"], row["channel"], row["freq_mhz"], row["channel_flags"],
            row["gps_lat"], row["gps_lon"], row["gps_fix"],
            row["session_id"], row["recorded_at"],
        ))
        obs_synced.append(row["obs_id"])

        if len(obs_batch) >= BATCH_SIZE:
            cur.executemany("""
                INSERT INTO mobile_observations
                    (mac, interface, scanner_host, signal_dbm, channel,
                     freq_mhz, channel_flags, gps_lat, gps_lon, gps_fix,
                     session_id, recorded_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, obs_batch)
            dst.commit()
            total_written += len(obs_batch)
            print(f"  ...{total_written} / {unsynced_count}", end="\r", flush=True)
            obs_batch.clear()

    if obs_batch:
        cur.executemany("""
            INSERT INTO mobile_observations
                (mac, interface, scanner_host, signal_dbm, channel,
                 freq_mhz, channel_flags, gps_lat, gps_lon, gps_fix,
                 session_id, recorded_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, obs_batch)
        dst.commit()
        total_written += len(obs_batch)

    print(f"\nWrote {total_written} observations to mobile_observations ({len(seen_macs)} devices)")

    # Mark synced in SQLite
    src.execute(
        f"UPDATE observations SET synced = 1 WHERE id IN ({','.join('?' * len(obs_synced))})",
        obs_synced
    )
    src.commit()

    # Mark sessions fully synced if all their obs are synced
    src.execute("""
        UPDATE sessions SET synced = 1
        WHERE id IN (
            SELECT session_id FROM observations
            GROUP BY session_id
            HAVING SUM(CASE WHEN synced = 0 THEN 1 ELSE 0 END) = 0
        )
    """)
    src.commit()

    cur.close()
    dst.close()
    src.close()
    print("Sync complete.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if args.db:
        db_path = Path(args.db)
    else:
        db_path = find_sqlite_db()

    if not db_path or not db_path.exists():
        print("ERROR: Could not find mobile_scan.db — use --db PATH to specify location")
        sys.exit(1)

    sync(db_path, dry_run=args.dry_run)
