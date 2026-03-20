import asyncio
import logging

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from api.db import get_db

router = APIRouter(prefix="/api/observations", tags=["observations"])
logger = logging.getLogger(__name__)

DEADLOCK_MAX_RETRIES = 3
DEADLOCK_RETRY_DELAY = 0.5  # seconds


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

            # Upsert devices (sorted by MAC to avoid deadlocks from lock ordering)
            for mac in sorted(device_rows):
                d = device_rows[mac]
                await db.execute(text("""
                    INSERT INTO devices
                        (mac, device_type, oui, is_randomized,
                         ht_capable, vht_capable, he_capable, first_seen, last_seen)
                    VALUES
                        (:mac, :type, :oui, :rand, :ht, :vht, :he, :first, :last)
                    ON DUPLICATE KEY UPDATE
                        last_seen   = GREATEST(last_seen,   VALUES(last_seen)),
                        ht_capable  = GREATEST(ht_capable,  VALUES(ht_capable)),
                        vht_capable = GREATEST(vht_capable, VALUES(vht_capable)),
                        he_capable  = GREATEST(he_capable,  VALUES(he_capable))
                """), {
                    "mac": mac, "type": d["type"], "oui": d["oui"],
                    "rand": d["is_randomized"],
                    "ht": d["ht"], "vht": d["vht"], "he": d["he"],
                    "first": d["first_seen"], "last": d["last_seen"],
                })

                for ssid in d["ssids"]:
                    if ssid and len(ssid) <= 32 and ssid.isprintable() and "\ufffd" not in ssid and "\x00" not in ssid:
                        await db.execute(text("""
                            INSERT IGNORE INTO ssids (mac, ssid, first_seen)
                            VALUES (:mac, :ssid, :ts)
                        """), {"mac": mac, "ssid": ssid, "ts": d["first_seen"]})

            # Insert observations
            inserted = 0
            for obs in observations:
                mac = obs.get("mac", "").lower()
                if mac not in device_rows:
                    continue
                await db.execute(text("""
                    INSERT INTO observations
                        (mac, interface, scanner_host, signal_dbm,
                         channel, freq_mhz, recorded_at)
                    VALUES
                        (:mac, :iface, :host, :sig, :ch, :freq, :ts)
                """), {
                    "mac":  mac,
                    "iface": obs.get("interface", "esp32-wifi"),
                    "host":  scanner_host,
                    "sig":   obs.get("signal_dbm"),
                    "ch":    obs.get("channel"),
                    "freq":  obs.get("freq_mhz"),
                    "ts":    obs.get("recorded_at"),
                })
                inserted += 1

            await db.commit()
            return {"inserted": inserted, "devices": len(device_rows)}

        except Exception as e:
            await db.rollback()
            if "1213" in str(e) and attempt < DEADLOCK_MAX_RETRIES:
                logger.warning("Deadlock on upload attempt %d/%d, retrying...",
                               attempt, DEADLOCK_MAX_RETRIES)
                await asyncio.sleep(DEADLOCK_RETRY_DELAY * attempt)
                continue
            raise
