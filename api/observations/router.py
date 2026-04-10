import asyncio
import logging

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from api.db import get_db

try:
    from scapy.all import conf as _scapy_conf
    def _oui_lookup(mac):
        try:
            m = _scapy_conf.manufdb._get_manuf(mac)
            return m if m else None
        except Exception:
            return None
except ImportError:
    def _oui_lookup(mac):
        return None

router = APIRouter(prefix="/api/observations", tags=["observations"])
logger = logging.getLogger(__name__)

DEADLOCK_MAX_RETRIES = 3
DEADLOCK_RETRY_DELAY = 0.5  # seconds
DEVICE_BATCH_SIZE = 50


def _chunks(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


def _build_device_values(batch, device_rows):
    """Build multi-row VALUES clause and params for device upsert."""
    parts = []
    params = {}
    for i, mac in enumerate(batch):
        d = device_rows[mac]
        parts.append(
            f"(:mac_{i}, :type_{i}, :oui_{i}, :mfr_{i}, :rand_{i},"
            f" :ht_{i}, :vht_{i}, :he_{i}, :first_{i}, :last_{i})"
        )
        params.update({
            f"mac_{i}": mac, f"type_{i}": d["type"], f"oui_{i}": d["oui"],
            f"mfr_{i}": _oui_lookup(mac),
            f"rand_{i}": d["is_randomized"],
            f"ht_{i}": d["ht"], f"vht_{i}": d["vht"], f"he_{i}": d["he"],
            f"first_{i}": d["first_seen"], f"last_{i}": d["last_seen"],
        })
    sql = text(
        "INSERT INTO devices"
        "  (mac, device_type, oui, manufacturer, is_randomized,"
        "   ht_capable, vht_capable, he_capable, first_seen, last_seen) "
        "VALUES " + ", ".join(parts) +
        " ON DUPLICATE KEY UPDATE"
        "  last_seen    = GREATEST(last_seen,   VALUES(last_seen)),"
        "  manufacturer = COALESCE(manufacturer, VALUES(manufacturer)),"
        "  ht_capable   = GREATEST(ht_capable,  VALUES(ht_capable)),"
        "  vht_capable  = GREATEST(vht_capable, VALUES(vht_capable)),"
        "  he_capable   = GREATEST(he_capable,  VALUES(he_capable))"
    )
    return sql, params


def _build_ssid_values(ssid_rows):
    """Build multi-row INSERT IGNORE for ssids."""
    parts = []
    params = {}
    for i, (mac, ssid, ts) in enumerate(ssid_rows):
        parts.append(f"(:mac_{i}, :ssid_{i}, :ts_{i})")
        params.update({f"mac_{i}": mac, f"ssid_{i}": ssid, f"ts_{i}": ts})
    sql = text(
        "INSERT IGNORE INTO ssids (mac, ssid, first_seen) VALUES "
        + ", ".join(parts)
    )
    return sql, params


def _build_obs_values(batch, scanner_host):
    """Build multi-row INSERT for observations."""
    parts = []
    params = {}
    for i, obs in enumerate(batch):
        parts.append(
            f"(:mac_{i}, :iface_{i}, :host_{i}, :sig_{i},"
            f" :ch_{i}, :freq_{i}, :pc_{i}, :ts_{i})"
        )
        params.update({
            f"mac_{i}":  obs["mac"],
            f"iface_{i}": obs.get("interface", "esp32-wifi"),
            f"host_{i}":  scanner_host,
            f"sig_{i}":   obs.get("signal_dbm"),
            f"ch_{i}":    obs.get("channel"),
            f"freq_{i}":  obs.get("freq_mhz"),
            f"pc_{i}":    obs.get("probe_count", 1),
            f"ts_{i}":    obs.get("recorded_at"),
        })
    sql = text(
        "INSERT INTO observations"
        "  (mac, interface, scanner_host, signal_dbm,"
        "   channel, freq_mhz, probe_count, recorded_at) "
        "VALUES " + ", ".join(parts)
    )
    return sql, params


@router.post("/upload")
async def upload_observations(body: dict, db: AsyncSession = Depends(get_db)):
    """
    Receive a batch of observations from a remote scanner (e.g. ESP32) that
    cannot write to MySQL directly.

    Expected body:
    {
        "scanner_host": "esp32-static-1",
        "observations": [
            {
                "mac":         "aa:bb:cc:dd:ee:ff",
                "device_type": "AP",          // "AP" or "Client"
                "signal_dbm":  -65,
                "channel":     6,
                "freq_mhz":    2437,
                "ssid":        "MyNetwork",   // optional, "" if none
                "ht":          false,
                "vht":         false,
                "he":          false,
                "probe_count": 7,             // optional, raw packets in 10s window (default 1)
                "recorded_at": "2026-03-17T01:00:00"
            },
            ...
        ]
    }
    """
    scanner_host = body.get("scanner_host", "unknown")
    observations = body.get("observations", [])
    health = body.get("health")

    # ── Insert scanner health if provided ────────────────────────────────────
    if health:
        try:
            await db.execute(text("""
                INSERT INTO scanner_health
                    (scanner_host, mac, free_heap, min_free_heap,
                     uptime_ms, temperature_c, recorded_at)
                VALUES
                    (:host, :mac, :free_heap, :min_free_heap,
                     :uptime_ms, :temperature_c, NOW())
            """), {
                "host":          scanner_host,
                "mac":           health.get("mac"),
                "free_heap":     health.get("free_heap"),
                "min_free_heap": health.get("min_free_heap"),
                "uptime_ms":     health.get("uptime_ms"),
                "temperature_c": health.get("temperature_c"),
            })
            await db.commit()
        except Exception as e:
            logger.error("Failed to insert scanner_health: %s", e)
            await db.rollback()

    if not observations:
        return {"inserted": 0}

    # ── Aggregate device metadata across the batch ─────────────────────────────
    device_rows = {}
    for obs in observations:
        mac = obs.get("mac", "").lower()
        if not mac or mac == "ff:ff:ff:ff:ff:ff":
            continue

        ts = obs.get("recorded_at")

        if mac not in device_rows:
            device_rows[mac] = {
                "type":          obs.get("device_type", "Client"),
                "oui":           mac[:8].upper(),
                "is_randomized": int(bool(int(mac.split(":")[0], 16) & 0x02)),
                "ht":  0, "vht": 0, "he": 0,
                "ssids": set(),
                "first_seen": ts,
                "last_seen":  ts,
            }

        d = device_rows[mac]
        if ts and (not d["last_seen"] or ts > d["last_seen"]):
            d["last_seen"] = ts
        if obs.get("ht"):  d["ht"]  = 1
        if obs.get("vht"): d["vht"] = 1
        if obs.get("he"):  d["he"]  = 1
        ssid = obs.get("ssid", "")
        if ssid:
            d["ssids"].add(ssid)

    # ── Write to DB with deadlock retry ────────────────────────────────────────
    for attempt in range(1, DEADLOCK_MAX_RETRIES + 1):
        try:
            # Scanner heartbeat
            await db.execute(text("""
                INSERT INTO scanners (hostname, label, is_active, last_heartbeat)
                VALUES (:host, :label, FALSE, NOW())
                ON DUPLICATE KEY UPDATE last_heartbeat = NOW()
            """), {"host": scanner_host, "label": scanner_host})

            # Upsert devices in batches of 50 (sorted by MAC to avoid deadlocks)
            sorted_macs = sorted(device_rows)
            for batch in _chunks(sorted_macs, DEVICE_BATCH_SIZE):
                sql, params = _build_device_values(batch, device_rows)
                await db.execute(sql, params)

            # Batch SSID inserts
            ssid_rows = []
            for mac in sorted_macs:
                d = device_rows[mac]
                for ssid in d["ssids"]:
                    if ssid and len(ssid) <= 32 and ssid.isprintable() and "\ufffd" not in ssid and "\x00" not in ssid:
                        ssid_rows.append((mac, ssid, d["first_seen"]))
            for batch in _chunks(ssid_rows, DEVICE_BATCH_SIZE):
                sql, params = _build_ssid_values(batch)
                await db.execute(sql, params)

            # Batch observation inserts
            valid_obs = []
            for obs in observations:
                mac = obs.get("mac", "").lower()
                if mac in device_rows:
                    valid_obs.append({**obs, "mac": mac})
            for batch in _chunks(valid_obs, DEVICE_BATCH_SIZE):
                sql, params = _build_obs_values(batch, scanner_host)
                await db.execute(sql, params)

            await db.commit()
            return {"inserted": len(valid_obs), "devices": len(device_rows)}

        except Exception as e:
            await db.rollback()
            if "1213" in str(e) and attempt < DEADLOCK_MAX_RETRIES:
                logger.warning("Deadlock on upload attempt %d/%d, retrying...",
                               attempt, DEADLOCK_MAX_RETRIES)
                await asyncio.sleep(DEADLOCK_RETRY_DELAY * attempt)
                continue
            raise
