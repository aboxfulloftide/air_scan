from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from api.db import get_db

router = APIRouter(prefix="/api/mobile", tags=["mobile"])


@router.get("/sessions")
async def list_sessions(db: AsyncSession = Depends(get_db)):
    """List all mobile scan sessions with summary stats."""
    r = await db.execute(text("""
        SELECT session_id,
               scanner_host,
               CONVERT_TZ(MIN(recorded_at), '+00:00', '-04:00') AS started_at,
               CONVERT_TZ(MAX(recorded_at), '+00:00', '-04:00') AS ended_at,
               COUNT(*) AS observation_count,
               COUNT(DISTINCT mac) AS device_count,
               SUM(gps_fix = 1) AS gps_fix_count,
               AVG(CASE WHEN gps_fix = 1 THEN gps_lat END) AS avg_lat,
               AVG(CASE WHEN gps_fix = 1 THEN gps_lon END) AS avg_lon
        FROM mobile_observations
        WHERE session_id IS NOT NULL
        GROUP BY session_id, scanner_host
        ORDER BY started_at DESC
    """))
    return [dict(row) for row in r.mappings().all()]


@router.get("/observations")
async def get_observations(
    session_id: str = Query(None),
    minutes: int = Query(None),
    mac: str = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Get mobile observations with GPS coords for map display.
    Filter by session, time window, or specific MAC."""
    conditions = ["gps_fix = 1", "gps_lat IS NOT NULL", "gps_lon IS NOT NULL"]
    params = {}

    if session_id:
        conditions.append("mo.session_id = :session_id")
        params["session_id"] = session_id
    elif minutes:
        conditions.append("mo.recorded_at >= NOW() - INTERVAL :minutes MINUTE")
        params["minutes"] = minutes
    else:
        # Default: last 60 minutes
        conditions.append("mo.recorded_at >= NOW() - INTERVAL 60 MINUTE")

    if mac:
        conditions.append("mo.mac = :mac")
        params["mac"] = mac

    where = " AND ".join(conditions)

    r = await db.execute(text(f"""
        SELECT mo.mac, mo.signal_dbm, mo.channel, mo.freq_mhz,
               mo.gps_lat, mo.gps_lon,
               CONVERT_TZ(mo.recorded_at, '+00:00', '-04:00') AS recorded_at,
               mo.session_id,
               d.device_type, d.manufacturer,
               kd.label AS known_label, kd.owner,
               (SELECT GROUP_CONCAT(s.ssid SEPARATOR ', ')
                FROM ssids s WHERE s.mac = mo.mac
                AND s.ssid REGEXP '^[[:print:]]+$' AND CHAR_LENGTH(s.ssid) BETWEEN 1 AND 32
                LIMIT 3) AS ssids
        FROM mobile_observations mo
        JOIN devices d ON d.mac = mo.mac
        LEFT JOIN known_devices kd ON kd.mac = mo.mac
        WHERE {where}
        ORDER BY mo.recorded_at DESC
    """), params)
    return [dict(row) for row in r.mappings().all()]


@router.get("/heatmap")
async def get_heatmap(
    session_id: str = Query(None),
    minutes: int = Query(None),
    mac: str = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Get aggregated GPS points for heatmap display.
    Groups nearby observations and returns avg position + count."""
    conditions = ["gps_fix = 1", "gps_lat IS NOT NULL", "gps_lon IS NOT NULL"]
    params = {}

    if session_id:
        conditions.append("session_id = :session_id")
        params["session_id"] = session_id
    elif minutes:
        conditions.append("recorded_at >= NOW() - INTERVAL :minutes MINUTE")
        params["minutes"] = minutes
    else:
        conditions.append("recorded_at >= NOW() - INTERVAL 60 MINUTE")

    if mac:
        conditions.append("mac = :mac")
        params["mac"] = mac

    where = " AND ".join(conditions)

    # Grid-snap to ~11m cells (0.0001 degree) for grouping
    r = await db.execute(text(f"""
        SELECT ROUND(gps_lat, 4) AS lat,
               ROUND(gps_lon, 4) AS lon,
               COUNT(*) AS obs_count,
               COUNT(DISTINCT mac) AS device_count,
               AVG(signal_dbm) AS avg_signal
        FROM mobile_observations
        WHERE {where}
        GROUP BY ROUND(gps_lat, 4), ROUND(gps_lon, 4)
        ORDER BY obs_count DESC
    """), params)
    return [dict(row) for row in r.mappings().all()]


@router.get("/track/{mac}")
async def get_device_track(
    mac: str,
    session_id: str = Query(None),
    minutes: int = Query(60),
    db: AsyncSession = Depends(get_db),
):
    """Get GPS trail for a specific device over time."""
    conditions = ["gps_fix = 1", "gps_lat IS NOT NULL", "mac = :mac"]
    params = {"mac": mac}

    if session_id:
        conditions.append("session_id = :session_id")
        params["session_id"] = session_id
    else:
        conditions.append("recorded_at >= NOW() - INTERVAL :minutes MINUTE")
        params["minutes"] = minutes

    where = " AND ".join(conditions)

    r = await db.execute(text(f"""
        SELECT gps_lat, gps_lon, signal_dbm,
               CONVERT_TZ(recorded_at, '+00:00', '-04:00') AS recorded_at
        FROM mobile_observations
        WHERE {where}
        ORDER BY recorded_at ASC
    """), params)
    return [dict(row) for row in r.mappings().all()]
