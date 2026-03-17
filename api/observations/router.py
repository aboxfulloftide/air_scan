from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from api.db import get_db

router = APIRouter(prefix="/api/observations", tags=["observations"])


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

    if not observations:
        return {"inserted": 0}

    # ── Scanner heartbeat ──────────────────────────────────────────────────────
    await db.execute(text("""
        INSERT INTO scanners (hostname, label, is_active, last_heartbeat)
        VALUES (:host, :label, FALSE, NOW())
        ON DUPLICATE KEY UPDATE last_heartbeat = NOW()
    """), {"host": scanner_host, "label": scanner_host})

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

    # ── Upsert devices ─────────────────────────────────────────────────────────
    for mac, d in device_rows.items():
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
            await db.execute(text("""
                INSERT IGNORE INTO ssids (mac, ssid, first_seen)
                VALUES (:mac, :ssid, :ts)
            """), {"mac": mac, "ssid": ssid, "ts": d["first_seen"]})

    # ── Insert observations ────────────────────────────────────────────────────
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
