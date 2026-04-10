import asyncio
import logging
from typing import List

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from api.db import get_db, AsyncSessionLocal
from api.mobile.analysis import analyze_session, analyze_all_unanalyzed

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/mobile", tags=["mobile"])


@router.get("/sessions")
async def list_sessions(db: AsyncSession = Depends(get_db)):
    """List all mobile scan sessions with summary stats and route metadata."""
    r = await db.execute(text("""
        SELECT mo.session_id,
               mo.scanner_host,
               CONVERT_TZ(MIN(mo.recorded_at), '+00:00', '-04:00') AS started_at,
               CONVERT_TZ(MAX(mo.recorded_at), '+00:00', '-04:00') AS ended_at,
               COUNT(*) AS observation_count,
               COUNT(DISTINCT mo.mac) AS device_count,
               SUM(mo.gps_fix = 1) AS gps_fix_count,
               AVG(CASE WHEN mo.gps_fix = 1 THEN mo.gps_lat END) AS avg_lat,
               AVG(CASE WHEN mo.gps_fix = 1 THEN mo.gps_lon END) AS avg_lon,
               sm.auto_name,
               sm.custom_name,
               sm.route_group,
               sm.start_address,
               sm.end_address,
               sm.analyzed_at
        FROM mobile_observations mo
        LEFT JOIN session_meta sm ON sm.session_id = mo.session_id
        WHERE mo.session_id IS NOT NULL
        GROUP BY mo.session_id, mo.scanner_host,
                 sm.auto_name, sm.custom_name, sm.route_group,
                 sm.start_address, sm.end_address, sm.analyzed_at
        ORDER BY started_at DESC
    """))
    return [dict(row) for row in r.mappings().all()]


async def _run_analysis_background(session_id: str | None = None):
    """Run analysis in background with its own DB session."""
    async with AsyncSessionLocal() as db:
        try:
            if session_id:
                await analyze_session(session_id, db)
            else:
                await analyze_all_unanalyzed(db)
        except Exception as e:
            logger.error("Background analysis failed: %s", e)


@router.post("/sessions/analyze")
async def analyze_sessions(
    session_id: str = Query(None, description="Analyze a specific session, or omit for all unanalyzed"),
    background_tasks: BackgroundTasks = None,
):
    """Kick off session route analysis in the background. Returns immediately."""
    background_tasks.add_task(_run_analysis_background, session_id)
    return {"status": "analyzing", "session_id": session_id}


class RenameSessionRequest(BaseModel):
    name: str


@router.put("/sessions/{session_id}/name")
async def rename_session(session_id: str, req: RenameSessionRequest, db: AsyncSession = Depends(get_db)):
    """Set a custom name for a session. Future similar routes will inherit this name + date."""
    await db.execute(text("""
        INSERT INTO session_meta (session_id, custom_name)
        VALUES (:sid, :name)
        ON DUPLICATE KEY UPDATE custom_name = :name
    """), {"sid": session_id, "name": req.name})
    await db.commit()
    return {"ok": True, "session_id": session_id, "custom_name": req.name}


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, db: AsyncSession = Depends(get_db)):
    """Delete a mobile scan session and all its observations."""
    await db.execute(text("DELETE FROM mobile_observations WHERE session_id = :sid"), {"sid": session_id})
    await db.execute(text("DELETE FROM session_meta WHERE session_id = :sid"), {"sid": session_id})
    await db.commit()
    return {"ok": True, "session_id": session_id}


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
               kd.label AS known_label, kd.owner, kd.status AS known_status,
               kd.port_scan_host_id,
               ssid_agg.ssids
        FROM mobile_observations mo
        JOIN devices d ON d.mac = mo.mac
        LEFT JOIN known_devices kd ON kd.mac = mo.mac
        LEFT JOIN (
            SELECT mac, GROUP_CONCAT(ssid SEPARATOR ', ') AS ssids
            FROM ssids
            WHERE ssid REGEXP '^[[:print:]]+$' AND CHAR_LENGTH(ssid) BETWEEN 1 AND 32
            GROUP BY mac
        ) ssid_agg ON ssid_agg.mac = mo.mac
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
    conditions = ["mo.gps_fix = 1", "mo.gps_lat IS NOT NULL", "mo.mac = :mac"]
    params = {"mac": mac}

    if session_id:
        conditions.append("mo.session_id = :session_id")
        params["session_id"] = session_id
    else:
        conditions.append("mo.recorded_at >= NOW() - INTERVAL :minutes MINUTE")
        params["minutes"] = minutes

    where = " AND ".join(conditions)

    r = await db.execute(text(f"""
        SELECT mo.gps_lat, mo.gps_lon, mo.signal_dbm,
               CONVERT_TZ(mo.recorded_at, '+00:00', '-04:00') AS recorded_at,
               d.device_type, d.manufacturer,
               kd.label AS known_label, kd.owner, kd.status AS known_status
        FROM mobile_observations mo
        JOIN devices d ON d.mac = mo.mac
        LEFT JOIN known_devices kd ON kd.mac = mo.mac
        WHERE {where}
        ORDER BY mo.recorded_at ASC
    """), params)
    return [dict(row) for row in r.mappings().all()]


# ── Session comparison ──

@router.get("/compare")
async def compare_sessions(
    session_ids: str = Query(..., description="Comma-separated session IDs"),
    mode: str = Query("matched", description="'matched' = MACs in 2+ sessions, 'delta' = MACs in exactly 1 session"),
    db: AsyncSession = Depends(get_db),
):
    """Compare multiple drive sessions. Returns observations annotated with
    which session(s) each MAC appeared in and how many sessions it spans."""
    ids = [s.strip() for s in session_ids.split(",") if s.strip()]
    if len(ids) < 2:
        return {"error": "Need at least 2 session IDs"}

    # Build parameterized IN clause
    id_params = {f"sid_{i}": sid for i, sid in enumerate(ids)}
    in_clause = ", ".join(f":sid_{i}" for i in range(len(ids)))

    # Step 1: Find which MACs appear in which sessions
    mac_sessions_sql = text(f"""
        SELECT mac, GROUP_CONCAT(DISTINCT session_id ORDER BY session_id) AS sessions,
               COUNT(DISTINCT session_id) AS session_count
        FROM mobile_observations
        WHERE session_id IN ({in_clause})
          AND gps_fix = 1 AND gps_lat IS NOT NULL AND gps_lon IS NOT NULL
        GROUP BY mac
    """)
    mac_rows = await db.execute(mac_sessions_sql, id_params)
    mac_info = {}
    for row in mac_rows.mappings().all():
        mac_info[row["mac"]] = {
            "sessions": row["sessions"],
            "session_count": row["session_count"],
        }

    # Step 2: Filter MACs by mode
    if mode == "matched":
        keep_macs = {m for m, info in mac_info.items() if info["session_count"] >= 2}
    else:  # delta
        keep_macs = {m for m, info in mac_info.items() if info["session_count"] == 1}

    if not keep_macs:
        return {"observations": [], "summary": {
            "total_macs": len(mac_info),
            "matched_macs": sum(1 for i in mac_info.values() if i["session_count"] >= 2),
            "delta_macs": sum(1 for i in mac_info.values() if i["session_count"] == 1),
            "sessions": ids,
        }}

    # Step 3: Fetch full observations for qualifying MACs
    mac_params = {f"mac_{i}": m for i, m in enumerate(keep_macs)}
    mac_in = ", ".join(f":mac_{i}" for i in range(len(keep_macs)))
    all_params = {**id_params, **mac_params}

    obs_sql = text(f"""
        SELECT mo.mac, mo.signal_dbm, mo.channel, mo.freq_mhz,
               mo.gps_lat, mo.gps_lon,
               CONVERT_TZ(mo.recorded_at, '+00:00', '-04:00') AS recorded_at,
               mo.session_id,
               d.device_type, d.manufacturer,
               kd.label AS known_label, kd.owner, kd.status AS known_status,
               kd.port_scan_host_id,
               ssid_agg.ssids
        FROM mobile_observations mo
        JOIN devices d ON d.mac = mo.mac
        LEFT JOIN known_devices kd ON kd.mac = mo.mac
        LEFT JOIN (
            SELECT mac, GROUP_CONCAT(ssid SEPARATOR ', ') AS ssids
            FROM ssids
            WHERE ssid REGEXP '^[[:print:]]+$' AND CHAR_LENGTH(ssid) BETWEEN 1 AND 32
            GROUP BY mac
        ) ssid_agg ON ssid_agg.mac = mo.mac
        WHERE mo.session_id IN ({in_clause})
          AND mo.mac IN ({mac_in})
          AND mo.gps_fix = 1 AND mo.gps_lat IS NOT NULL AND mo.gps_lon IS NOT NULL
        ORDER BY mo.session_id, mo.recorded_at DESC
    """)
    r = await db.execute(obs_sql, all_params)
    observations = []
    for row in r.mappings().all():
        obs = dict(row)
        info = mac_info.get(obs["mac"], {})
        obs["session_count"] = info.get("session_count", 1)
        obs["seen_in_sessions"] = info.get("sessions", obs["session_id"])
        observations.append(obs)

    return {
        "observations": observations,
        "summary": {
            "total_macs": len(mac_info),
            "matched_macs": sum(1 for i in mac_info.values() if i["session_count"] >= 2),
            "delta_macs": sum(1 for i in mac_info.values() if i["session_count"] == 1),
            "sessions": ids,
        },
    }


# ── Ignored MACs ──

class IgnoreMacRequest(BaseModel):
    mac: str
    ssids: str | None = None
    reason: str | None = None


@router.get("/ignored")
async def list_ignored(db: AsyncSession = Depends(get_db)):
    """List all ignored MACs."""
    r = await db.execute(text(
        "SELECT mac, ssids, reason, ignored_at FROM ignored_macs ORDER BY ignored_at DESC"
    ))
    return [dict(row) for row in r.mappings().all()]


@router.post("/ignored")
async def add_ignored(req: IgnoreMacRequest, db: AsyncSession = Depends(get_db)):
    """Add a MAC to the ignore list."""
    await db.execute(text(
        "INSERT IGNORE INTO ignored_macs (mac, ssids, reason) VALUES (:mac, :ssids, :reason)"
    ), {"mac": req.mac, "ssids": req.ssids, "reason": req.reason})
    await db.commit()
    return {"ok": True}


@router.delete("/ignored/{mac}")
async def remove_ignored(mac: str, db: AsyncSession = Depends(get_db)):
    """Remove a MAC from the ignore list."""
    await db.execute(text("DELETE FROM ignored_macs WHERE mac = :mac"), {"mac": mac})
    await db.commit()
    return {"ok": True}


@router.delete("/ignored")
async def clear_ignored(db: AsyncSession = Depends(get_db)):
    """Clear all ignored MACs."""
    await db.execute(text("DELETE FROM ignored_macs"))
    await db.commit()
    return {"ok": True}
