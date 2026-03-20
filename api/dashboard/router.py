from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from api.db import get_db

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/")
async def get_dashboard(db: AsyncSession = Depends(get_db)):
    # Device counts
    r = await db.execute(text("""
        SELECT
            COUNT(*) as total_devices,
            SUM(device_type = 'AP') as total_aps,
            SUM(device_type = 'Client') as total_clients,
            SUM(last_seen >= NOW() - INTERVAL 5 MINUTE) as active_devices,
            SUM(last_seen >= NOW() - INTERVAL 1 HOUR) as active_1h,
            SUM(last_seen >= NOW() - INTERVAL 24 HOUR) as active_24h,
            SUM(is_randomized = 1) as randomized_macs
        FROM devices
    """))
    row = r.mappings().first()

    # Known device breakdown
    r2 = await db.execute(text("""
        SELECT
            COALESCE(kd.status, 'unclassified') as status,
            COUNT(*) as count
        FROM devices d
        LEFT JOIN known_devices kd ON d.mac = kd.mac
        GROUP BY COALESCE(kd.status, 'unclassified')
    """))
    known_breakdown = {r["status"]: r["count"] for r in r2.mappings().all()}

    # Scanner health
    r3 = await db.execute(text("""
        SELECT hostname, label, is_active, last_heartbeat,
            CASE
                WHEN last_heartbeat >= NOW() - INTERVAL 2 MINUTE THEN 'online'
                WHEN last_heartbeat >= NOW() - INTERVAL 10 MINUTE THEN 'stale'
                ELSE 'offline'
            END as health
        FROM scanners
        ORDER BY hostname
    """))
    scanners = [dict(r) for r in r3.mappings().all()]

    # Recent observation rate (observations per minute, last 10 min)
    r4 = await db.execute(text("""
        SELECT COUNT(*) / 10.0 as obs_per_minute
        FROM observations
        WHERE recorded_at >= NOW() - INTERVAL 10 MINUTE
    """))
    obs_rate = r4.scalar() or 0

    # Top SSIDs (last 24h)
    r5 = await db.execute(text("""
        SELECT s.ssid, COUNT(DISTINCT s.mac) as device_count
        FROM ssids s
        JOIN devices d ON d.mac = s.mac
        WHERE d.last_seen >= NOW() - INTERVAL 24 HOUR
        AND s.ssid REGEXP '^[[:print:]]+$' AND CHAR_LENGTH(s.ssid) BETWEEN 1 AND 32
        GROUP BY s.ssid
        ORDER BY device_count DESC
        LIMIT 10
    """))
    top_ssids = [dict(r) for r in r5.mappings().all()]

    return {
        "total_devices": row["total_devices"] or 0,
        "total_aps": row["total_aps"] or 0,
        "total_clients": row["total_clients"] or 0,
        "active_devices": row["active_devices"] or 0,
        "active_1h": row["active_1h"] or 0,
        "active_24h": row["active_24h"] or 0,
        "randomized_macs": row["randomized_macs"] or 0,
        "obs_per_minute": round(float(obs_rate), 1),
        "known_breakdown": known_breakdown,
        "scanners": scanners,
        "top_ssids": top_ssids,
    }
