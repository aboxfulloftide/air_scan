from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from api.db import get_db

router = APIRouter(prefix="/api/scanners", tags=["scanners"])


@router.get("/")
async def list_scanners(db: AsyncSession = Depends(get_db)):
    r = await db.execute(text("""
        SELECT s.*,
            CASE
                WHEN last_heartbeat >= NOW() - INTERVAL 2 MINUTE THEN 'online'
                WHEN last_heartbeat >= NOW() - INTERVAL 10 MINUTE THEN 'stale'
                ELSE 'offline'
            END as health,
            COALESCE(rc.recent_obs, 0) as recent_obs,
            COALESCE(rc.device_count, 0) as device_count,
            COALESCE(rc.total_probes, 0) as total_probes,
            rc.avg_probes_per_device
        FROM scanners s
        LEFT JOIN (
            SELECT scanner_host,
                   COUNT(*) AS recent_obs,
                   COUNT(DISTINCT mac) AS device_count,
                   SUM(probe_count) AS total_probes,
                   ROUND(SUM(probe_count) / NULLIF(COUNT(DISTINCT mac), 0), 1) AS avg_probes_per_device
            FROM observations
            WHERE recorded_at >= UTC_TIMESTAMP() - INTERVAL 10 MINUTE
            GROUP BY scanner_host
        ) rc ON rc.scanner_host = s.hostname
        ORDER BY hostname
    """))
    return [dict(r) for r in r.mappings().all()]


@router.patch("/{scanner_id}")
async def update_scanner(scanner_id: int, body: dict, db: AsyncSession = Depends(get_db)):
    """Update scanner label and position. x_pos/y_pos are lat/lon, z_pos is height offset in feet."""
    updates = []
    params = {"id": scanner_id}

    for field in ("label", "x_pos", "y_pos", "z_pos", "floor"):
        if field in body:
            updates.append(f"{field} = :{field}")
            params[field] = body[field]

    if not updates:
        return {"error": "nothing to update"}, 400

    await db.execute(
        text(f"UPDATE scanners SET {', '.join(updates)} WHERE id = :id"),
        params
    )
    await db.commit()
    return {"ok": True}


@router.delete("/{scanner_id}")
async def delete_scanner(scanner_id: int, db: AsyncSession = Depends(get_db)):
    """Delete a scanner entry."""
    await db.execute(text("DELETE FROM scanners WHERE id = :id"), {"id": scanner_id})
    await db.commit()
    return {"ok": True}


@router.get("/stats/{hostname}")
async def scanner_stats(hostname: str, minutes: int = Query(10), db: AsyncSession = Depends(get_db)):
    """Get recent scan results for a specific scanner."""
    # Summary counts
    summary = await db.execute(text("""
        SELECT COUNT(*) AS obs_count,
               COUNT(DISTINCT o.mac) AS device_count,
               COUNT(DISTINCT CASE WHEN d.device_type = 'AP' THEN o.mac END) AS ap_count,
               COUNT(DISTINCT CASE WHEN d.device_type = 'Client' THEN o.mac END) AS client_count,
               AVG(o.signal_dbm) AS avg_signal,
               MIN(o.signal_dbm) AS min_signal,
               MAX(o.signal_dbm) AS max_signal
        FROM observations o
        JOIN devices d ON d.mac = o.mac
        WHERE o.scanner_host = :host
          AND o.recorded_at >= UTC_TIMESTAMP() - INTERVAL :minutes MINUTE
    """), {"host": hostname, "minutes": minutes})
    stats = dict(summary.mappings().first() or {})

    # Top devices by observation count
    top_devices = await db.execute(text("""
        SELECT o.mac, d.device_type, d.manufacturer,
               kd.label AS known_label, kd.owner,
               COUNT(*) AS slot_count,
               SUM(o.probe_count) AS total_probes,
               ROUND(SUM(o.probe_count) / NULLIF(COUNT(*), 0), 1) AS avg_probes_per_slot,
               MAX(o.signal_dbm) AS best_signal,
               AVG(o.signal_dbm) AS avg_signal,
               (SELECT GROUP_CONCAT(s.ssid SEPARATOR ', ')
                FROM ssids s WHERE s.mac = o.mac
                AND s.ssid REGEXP '^[[:print:]]+$' AND CHAR_LENGTH(s.ssid) BETWEEN 1 AND 32
                LIMIT 3) AS ssids
        FROM observations o
        JOIN devices d ON d.mac = o.mac
        LEFT JOIN known_devices kd ON kd.mac = o.mac
        WHERE o.scanner_host = :host
          AND o.recorded_at >= UTC_TIMESTAMP() - INTERVAL :minutes MINUTE
        GROUP BY o.mac, d.device_type, d.manufacturer, kd.label, kd.owner
        ORDER BY obs_count DESC
        LIMIT 25
    """), {"host": hostname, "minutes": minutes})

    # Channel distribution
    channels = await db.execute(text("""
        SELECT channel, freq_mhz, COUNT(*) AS obs_count,
               COUNT(DISTINCT mac) AS device_count
        FROM observations
        WHERE scanner_host = :host
          AND recorded_at >= UTC_TIMESTAMP() - INTERVAL :minutes MINUTE
          AND channel IS NOT NULL
        GROUP BY channel, freq_mhz
        ORDER BY obs_count DESC
    """), {"host": hostname, "minutes": minutes})

    return {
        "hostname": hostname,
        "minutes": minutes,
        **stats,
        "top_devices": [dict(r) for r in top_devices.mappings().all()],
        "channels": [dict(r) for r in channels.mappings().all()],
    }
