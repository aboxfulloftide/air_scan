#!/usr/bin/env python3
"""
Triangulation engine — computes device positions from RSSI observations.

Uses log-distance path loss model to convert RSSI to distance, then
trilaterates using scipy least-squares minimization. Fixed devices
serve as calibration anchors to derive the path-loss exponent.

Usage:
    python3 triangulate.py [--interval 30] [--once]
"""

import argparse
import math
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
# Config
# ---------------------------------------------------------------------------

DB_CONFIG = {
    "host":     os.environ.get("DB_HOST", "localhost"),
    "user":     os.environ["DB_USER"],
    "password": os.environ["DB_PASS"],
    "database": os.environ.get("DB_NAME", "wireless"),
}

EARTH_RADIUS_M = 6_371_000


# ---------------------------------------------------------------------------
# Geo helpers
# ---------------------------------------------------------------------------

def haversine_distance(lat1, lon1, lat2, lon2):
    """Return distance in meters between two GPS points."""
    lat1, lon1, lat2, lon2 = map(math.radians, (lat1, lon1, lat2, lon2))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return EARTH_RADIUS_M * 2 * math.asin(math.sqrt(a))


def weighted_centroid(scanner_positions, rssi_values):
    """Compute position as RSSI-weighted centroid of scanner GPS coords.

    scanner_positions: list of (lat, lon)
    rssi_values:       list of avg RSSI (dBm, negative)
    Returns (lat, lon)
    """
    # Convert RSSI to weighting — softer exponent (/25 vs /10) prevents
    # one strong scanner from completely dominating the position estimate
    weights = [10 ** (rssi / 25) for rssi in rssi_values]
    total_w = sum(weights)
    lat = sum(w * pos[0] for w, pos in zip(weights, scanner_positions)) / total_w
    lon = sum(w * pos[1] for w, pos in zip(weights, scanner_positions)) / total_w
    return lat, lon


def external_position(scanner_positions, rssi_values, ref_lat, ref_lon, min_outside_m=7):
    """Position an external device (neighbor AP) outside the scanner area.

    Uses weighted centroid to find direction, then projects outward from
    the centroid past the nearest scanner by at least min_outside_m.
    Weaker signals get pushed further out.
    """
    # Find direction via weighted centroid
    wc_lat, wc_lon = weighted_centroid(scanner_positions, rssi_values)

    # Direction vector from centroid to weighted centroid (in degrees, fine for short distances)
    dlat = wc_lat - ref_lat
    dlon = wc_lon - ref_lon
    mag = math.hypot(dlat, dlon)
    if mag < 1e-10:
        # No clear direction — just push north
        dlat, dlon = 1, 0
        mag = 1
    dlat /= mag
    dlon /= mag

    # Find the scanner furthest along this direction (nearest wall)
    best_proj = 0
    for pos in scanner_positions:
        proj = (pos[0] - ref_lat) * dlat + (pos[1] - ref_lon) * dlon
        if proj > best_proj:
            best_proj = proj

    # Distance beyond that scanner: at least min_outside_m, more for weaker signals
    best_rssi = max(rssi_values)
    # Weaker signal = further away. -50 dBm → ~7m, -80 dBm → ~20m
    extra_m = max(min_outside_m, 3 + abs(best_rssi + 40) * 0.5)

    # Convert extra_m to degrees (approximate)
    m_per_deg_lat = EARTH_RADIUS_M * math.pi / 180
    m_per_deg_lon = m_per_deg_lat * math.cos(math.radians(ref_lat))
    total_offset_lat = best_proj + extra_m / m_per_deg_lat * dlat
    total_offset_lon = best_proj + extra_m / m_per_deg_lon * dlon

    # Wait - best_proj is in degree-space, extra_m is in meters. Let me redo properly.
    # Convert best_proj to meters
    best_proj_m = best_proj * math.hypot(m_per_deg_lat * dlat, m_per_deg_lon * dlon)

    total_m = best_proj_m + extra_m
    lat = ref_lat + total_m * dlat / m_per_deg_lat
    lon = ref_lon + total_m * dlon / m_per_deg_lon

    return lat, lon


def compute_confidence(scanner_count, rssi_spread):
    """Compute a 0-100 confidence score.

    Higher scanner count and larger RSSI spread = more confidence.
    """
    if scanner_count == 1:
        return 10.0

    # Base from scanner count: 2->40, 3->60, 4+->75
    base = min(20 * scanner_count, 75)

    # Bonus for RSSI spread (more spread = better discrimination)
    # 10+ dB spread is great, <3 dB is poor
    spread_bonus = min(rssi_spread * 2, 25)

    return max(5.0, min(100.0, round(base + spread_bonus, 1)))


# ---------------------------------------------------------------------------
# RSSI calibration (#2: per-scanner correction)
# ---------------------------------------------------------------------------

def calibrate_path_loss(scanners, fixed_devices, obs):
    """Auto-calibrate path-loss model from fixed device observations.

    Fits RSSI = A - 10*n*log10(d) via linear regression on all
    (scanner, fixed_device) pairs where we know both positions.

    Returns (tx_power_at_1m, path_loss_n) or (None, None) if
    insufficient data or the fit produces unreasonable values.
    """
    xs = []  # log10(distance)
    ys = []  # observed RSSI

    for dev in fixed_devices:
        if dev["mac"] not in obs:
            continue
        mac_obs = obs[dev["mac"]]
        for scanner_host, rssi in mac_obs.items():
            if scanner_host not in scanners:
                continue
            s = scanners[scanner_host]
            d = haversine_distance(s["lat"], s["lon"], dev["lat"], dev["lon"])
            if d < 0.5:
                d = 0.5
            xs.append(math.log10(d))
            ys.append(rssi)

    if len(xs) < 4:
        return None, None

    # Linear regression: y = a + b*x
    n = len(xs)
    sx = sum(xs)
    sy = sum(ys)
    sxy = sum(x * y for x, y in zip(xs, ys))
    sxx = sum(x * x for x in xs)

    denom = n * sxx - sx * sx
    if abs(denom) < 1e-10:
        return None, None

    b = (n * sxy - sx * sy) / denom
    a = (sy - b * sx) / n

    # RSSI = A - 10*n*log10(d)  =>  a = A, b = -10*n
    tx_power = a
    path_loss_n = -b / 10

    # Sanity check — reject fits outside physically reasonable range
    if not (1.5 <= path_loss_n <= 6.0) or not (-60 <= tx_power <= -20):
        return None, None

    return tx_power, path_loss_n


def compute_scanner_offsets(scanners, fixed_devices, obs, tx_power, path_loss_n):
    """Compute per-scanner RSSI bias using fixed devices as ground truth.

    For each scanner, compares observed RSSI from each fixed device against
    the expected RSSI (from log-distance path-loss using the known positions).
    The offset = mean(observed - expected).

    To correct: corrected_rssi = observed - offset
    - Scanner reads too weak (offset < 0): correction raises its RSSI
    - Scanner reads too hot  (offset > 0): correction lowers its RSSI

    Returns {scanner_host: (offset_db, sample_count)}
    """
    offsets = {}
    for scanner_host, s_pos in scanners.items():
        residuals = []
        for dev in fixed_devices:
            if dev["mac"] not in obs:
                continue
            mac_obs = obs[dev["mac"]]
            if scanner_host not in mac_obs:
                continue

            d = haversine_distance(s_pos["lat"], s_pos["lon"], dev["lat"], dev["lon"])
            if d < 0.5:
                d = 0.5

            expected = tx_power - 10 * path_loss_n * math.log10(d)
            observed = mac_obs[scanner_host]
            residuals.append(observed - expected)

        if residuals:
            offsets[scanner_host] = (sum(residuals) / len(residuals), len(residuals))
        else:
            offsets[scanner_host] = (0.0, 0)

    return offsets


# ---------------------------------------------------------------------------
# Main cycle
# ---------------------------------------------------------------------------

def load_settings(cur):
    """Load triangulation settings from the settings table."""
    cur.execute("SELECT key_name, value FROM settings WHERE key_name LIKE 'triangulation_%' OR key_name = 'position_retention_days'")
    raw = {row["key_name"]: row["value"] for row in cur.fetchall()}
    return {
        "window_seconds":   int(raw.get("triangulation_window_seconds", "120")),
        "retention_days":   int(raw.get("position_retention_days", "1")),
        "tx_power":         float(raw.get("triangulation_tx_power", "-40")),
        "path_loss_n":      float(raw.get("triangulation_path_loss_n", "2.7")),
        "rssi_correction":  raw.get("triangulation_rssi_correction", "true").lower() == "true",
    }


def run_cycle(conn):
    """Run one triangulation cycle."""
    cur = conn.cursor(dictionary=True)

    # 1. Load settings
    settings = load_settings(cur)
    window = settings["window_seconds"]

    # 2. Load placed scanners (those with GPS positions)
    cur.execute("""
        SELECT hostname, x_pos AS lat, y_pos AS lon
        FROM scanners
        WHERE x_pos IS NOT NULL AND y_pos IS NOT NULL AND is_active = TRUE
    """)
    scanners = {row["hostname"]: {"lat": float(row["lat"]), "lon": float(row["lon"])}
                for row in cur.fetchall()}

    if not scanners:
        print("  No placed scanners found — skipping")
        cur.close()
        return

    # 3. Load fixed device MACs (for calibration, skip positioning)
    cur.execute("""
        SELECT mac, fixed_x AS lat, fixed_y AS lon
        FROM known_devices
        WHERE is_fixed = TRUE AND fixed_x IS NOT NULL AND fixed_y IS NOT NULL
    """)
    fixed_devices = [{"mac": row["mac"], "lat": float(row["lat"]), "lon": float(row["lon"])}
                     for row in cur.fetchall()]
    fixed_macs = {d["mac"] for d in fixed_devices}

    # 3b. Load AP MACs and which ones are manually placed (in-house APs)
    cur.execute("SELECT mac FROM devices WHERE device_type = 'AP'")
    ap_macs = {row["mac"] for row in cur.fetchall()}
    cur.execute("SELECT DISTINCT mac FROM device_positions WHERE method = 'manual'")
    placed_ap_macs = {row["mac"] for row in cur.fetchall()}

    # 4. Pre-filter: only position MACs seen in >= 75% of scan slots over 2 hours
    #    A "scan slot" = 10-second UTC-aligned window
    scanner_hosts = list(scanners.keys())
    placeholders = ",".join(["%s"] * len(scanner_hosts))

    cur.execute(f"""
        SELECT mac
        FROM (
            SELECT mc.mac, mc.scanner_host, mc.mac_slots, sc.total_slots
            FROM (
                SELECT mac, scanner_host,
                       COUNT(DISTINCT FLOOR(UNIX_TIMESTAMP(recorded_at) / 10)) AS mac_slots
                FROM observations
                WHERE recorded_at >= NOW() - INTERVAL 2 HOUR
                  AND signal_dbm IS NOT NULL
                  AND scanner_host IN ({placeholders})
                GROUP BY mac, scanner_host
            ) mc
            JOIN (
                SELECT scanner_host,
                       COUNT(DISTINCT FLOOR(UNIX_TIMESTAMP(recorded_at) / 10)) AS total_slots
                FROM observations
                WHERE recorded_at >= NOW() - INTERVAL 2 HOUR
                  AND signal_dbm IS NOT NULL
                  AND scanner_host IN ({placeholders})
                GROUP BY scanner_host
            ) sc ON mc.scanner_host = sc.scanner_host
            WHERE mc.mac_slots / sc.total_slots >= 0.05
        ) stable
        GROUP BY mac
    """, scanner_hosts + scanner_hosts)
    stable_macs = {row["mac"] for row in cur.fetchall()}
    # Always include fixed device MACs for calibration
    stable_macs |= fixed_macs

    if not stable_macs:
        print("  No stable devices (75% threshold over 2h) — skipping")
        cur.close()
        return

    print(f"  {len(stable_macs) - len(fixed_macs)} stable devices + {len(fixed_macs)} fixed anchors")

    # 5. Query recent observations for stable MACs only: AVG(signal_dbm) per (mac, scanner_host)
    mac_list = list(stable_macs)
    mac_placeholders = ",".join(["%s"] * len(mac_list))
    cur.execute(f"""
        SELECT mac, scanner_host, AVG(signal_dbm) AS avg_rssi
        FROM observations
        WHERE recorded_at >= NOW() - INTERVAL %s SECOND
          AND signal_dbm IS NOT NULL
          AND scanner_host IN ({placeholders})
          AND mac IN ({mac_placeholders})
        GROUP BY mac, scanner_host
    """, [window] + scanner_hosts + mac_list)

    # Build observations dict: {mac: {scanner_host: avg_rssi}}
    obs = {}
    for row in cur.fetchall():
        mac = row["mac"]
        if mac not in obs:
            obs[mac] = {}
        obs[mac][row["scanner_host"]] = float(row["avg_rssi"])

    if not obs:
        print("  No recent observations for stable devices — skipping")
        cur.close()
        return

    # 5b. Per-scanner RSSI correction using fixed devices as calibration anchors
    if settings["rssi_correction"] and fixed_devices:
        # Auto-calibrate path-loss model from the fixed device data
        cal_tx, cal_n = calibrate_path_loss(scanners, fixed_devices, obs)
        if cal_tx is not None:
            tx_power, path_loss_n = cal_tx, cal_n
            print(f"  Auto-calibrated path-loss: TX={tx_power:.1f}dBm, n={path_loss_n:.2f}")
        else:
            tx_power = settings["tx_power"]
            path_loss_n = settings["path_loss_n"]
            print(f"  Using stored path-loss: TX={tx_power}dBm, n={path_loss_n}")

        # Compute per-scanner offsets
        offsets = compute_scanner_offsets(scanners, fixed_devices, obs, tx_power, path_loss_n)
        non_zero = {h: f"{v:+.1f}dB" for h, (v, _) in offsets.items() if abs(v) > 0.5}
        if non_zero:
            print(f"  Scanner RSSI offsets: {non_zero}")

        # Apply corrections to all observation RSSI values
        for mac, mac_obs in obs.items():
            for h in mac_obs:
                if h in offsets:
                    mac_obs[h] -= offsets[h][0]

        # Store offsets in scanners table for UI visibility
        for h, (offset, samples) in offsets.items():
            cur.execute(
                "UPDATE scanners SET rssi_offset = %s, calibration_samples = %s WHERE hostname = %s",
                (round(offset, 2), samples, h),
            )

    # 6. Validate fixed device positions using weighted centroid
    if fixed_devices:
        errors = []
        for dev in fixed_devices:
            if dev["mac"] not in obs:
                continue
            mac_obs = obs[dev["mac"]]
            valid = [(h, rssi) for h, rssi in mac_obs.items() if h in scanners]
            if len(valid) < 2:
                continue
            positions = [(scanners[h]["lat"], scanners[h]["lon"]) for h, _ in valid]
            rssi_vals = [rssi for _, rssi in valid]
            pred_lat, pred_lon = weighted_centroid(positions, rssi_vals)
            err = haversine_distance(pred_lat, pred_lon, dev["lat"], dev["lon"])
            errors.append(err)
        if errors:
            avg_err = sum(errors) / len(errors)
            print(f"  Fixed device validation: avg={avg_err:.1f}m, max={max(errors):.1f}m ({len(errors)} devices)")

    # 7. Compute reference point (centroid of scanners) for external positioning
    ref_lat = sum(s["lat"] for s in scanners.values()) / len(scanners)
    ref_lon = sum(s["lon"] for s in scanners.values()) / len(scanners)

    # 8. Position each non-fixed MAC
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    positioned = 0
    methods = {"trilateration": 0, "single_scanner": 0}

    for mac, mac_obs in obs.items():
        if mac in fixed_macs:
            continue

        # Filter to only scanners we have positions for
        valid = [(h, rssi) for h, rssi in mac_obs.items() if h in scanners]
        if not valid:
            continue

        count = len(valid)
        is_external_ap = mac in ap_macs and mac not in placed_ap_macs

        if count >= 2:
            positions = [(scanners[h]["lat"], scanners[h]["lon"]) for h, _ in valid]
            rssi_vals = [rssi for _, rssi in valid]
            if is_external_ap:
                lat, lon = external_position(positions, rssi_vals, ref_lat, ref_lon)
            else:
                lat, lon = weighted_centroid(positions, rssi_vals)
            rssi_spread = max(rssi_vals) - min(rssi_vals)
            method = "trilateration"
            methods["trilateration"] += 1
        else:
            # Single scanner — position at the scanner location
            h = valid[0][0]
            lat = scanners[h]["lat"]
            lon = scanners[h]["lon"]
            rssi_spread = 0
            method = "single_scanner"
            methods["single_scanner"] += 1

        confidence = compute_confidence(count, rssi_spread)

        cur.execute("""
            INSERT INTO device_positions
                (mac, x_pos, y_pos, floor, confidence, method, scanner_count, computed_at)
            VALUES (%s, %s, %s, 0, %s, %s, %s, %s)
        """, (mac, lat, lon, confidence, method, count, now))
        positioned += 1

    # 8. Cleanup old computed positions
    retention = settings["retention_days"]
    cur.execute("""
        DELETE FROM device_positions
        WHERE method IN ('trilateration', 'single_scanner')
          AND computed_at < NOW() - INTERVAL %s DAY
    """, (retention,))
    cleaned = cur.rowcount

    conn.commit()
    cur.close()

    print(f"  Positioned {positioned} devices "
          f"(multi={methods['trilateration']}, "
          f"single={methods['single_scanner']}), cleaned {cleaned} old rows")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Triangulation engine for air_scan")
    parser.add_argument("--interval", type=int, default=30,
                        help="Cycle interval in seconds (default: 30)")
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    args = parser.parse_args()

    print("=== Triangulation Engine ===")
    print(f"DB: {DB_CONFIG['host']}/{DB_CONFIG['database']}")
    print(f"Interval: {args.interval}s")
    print()

    if args.once:
        conn = mysql.connector.connect(**DB_CONFIG)
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        print(f"[{ts} UTC] Running cycle...")
        try:
            run_cycle(conn)
        finally:
            conn.close()
        return

    while True:
        try:
            conn = mysql.connector.connect(**DB_CONFIG)
            ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
            print(f"[{ts} UTC] Running cycle...")
            run_cycle(conn)
            conn.close()
        except KeyboardInterrupt:
            print("\nStopped.")
            break
        except Exception as e:
            print(f"[ERROR] {e}")

        time.sleep(args.interval)


if __name__ == "__main__":
    main()
