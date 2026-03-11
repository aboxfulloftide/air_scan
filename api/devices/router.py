from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from api.db import get_db

router = APIRouter(prefix="/api/devices", tags=["devices"])


@router.get("/")
async def list_devices(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    device_type: str = Query(None),
    status: str = Query(None),
    search: str = Query(None),
    sort: str = Query("last_seen"),
    order: str = Query("desc"),
    db: AsyncSession = Depends(get_db),
):
    allowed_sorts = {"last_seen", "first_seen", "mac", "manufacturer"}
    if sort not in allowed_sorts:
        sort = "last_seen"
    if order not in ("asc", "desc"):
        order = "desc"

    conditions = []
    params = {}

    if device_type in ("AP", "Client"):
        conditions.append("d.device_type = :device_type")
        params["device_type"] = device_type

    if status in ("known", "unknown", "guest", "rogue"):
        conditions.append("kd.status = :status")
        params["status"] = status

    if search:
        conditions.append("(d.mac LIKE :search OR d.manufacturer LIKE :search OR s.ssid LIKE :search)")
        params["search"] = f"%{search}%"

    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    offset = (page - 1) * per_page

    # Count
    count_sql = f"""
        SELECT COUNT(DISTINCT d.mac)
        FROM devices d
        LEFT JOIN known_devices kd ON d.mac = kd.mac
        LEFT JOIN ssids s ON d.mac = s.mac
        {where}
    """
    r = await db.execute(text(count_sql), params)
    total = r.scalar()

    # Fetch
    data_sql = f"""
        SELECT d.mac, d.device_type, d.oui, d.manufacturer, d.is_randomized,
               d.ht_capable, d.vht_capable, d.he_capable,
               d.first_seen, d.last_seen,
               kd.status as known_status, kd.label as known_label, kd.owner,
               GROUP_CONCAT(DISTINCT s.ssid ORDER BY s.ssid SEPARATOR ', ') as ssids
        FROM devices d
        LEFT JOIN known_devices kd ON d.mac = kd.mac
        LEFT JOIN ssids s ON d.mac = s.mac
        {where}
        GROUP BY d.mac
        ORDER BY d.{sort} {order}
        LIMIT :limit OFFSET :offset
    """
    params["limit"] = per_page
    params["offset"] = offset

    r = await db.execute(text(data_sql), params)
    devices = [dict(row) for row in r.mappings().all()]

    return {"total": total, "page": page, "per_page": per_page, "devices": devices}


@router.get("/{mac}")
async def get_device(mac: str, db: AsyncSession = Depends(get_db)):
    # Device info
    r = await db.execute(text("""
        SELECT d.*, kd.status as known_status, kd.label as known_label,
               kd.owner, kd.port_scan_host_id
        FROM devices d
        LEFT JOIN known_devices kd ON d.mac = kd.mac
        WHERE d.mac = :mac
    """), {"mac": mac})
    device = r.mappings().first()
    if not device:
        return {"error": "not found"}, 404

    # SSIDs
    r2 = await db.execute(text(
        "SELECT ssid, first_seen FROM ssids WHERE mac = :mac ORDER BY first_seen"
    ), {"mac": mac})
    ssids = [dict(r) for r in r2.mappings().all()]

    # Recent observations (last 1h, grouped by scanner)
    r3 = await db.execute(text("""
        SELECT scanner_host, signal_dbm, channel, freq_mhz, channel_flags, recorded_at
        FROM observations
        WHERE mac = :mac AND recorded_at >= NOW() - INTERVAL 1 HOUR
        ORDER BY recorded_at DESC
        LIMIT 100
    """), {"mac": mac})
    observations = [dict(r) for r in r3.mappings().all()]

    # Signal history (last 24h, one per minute per scanner)
    r4 = await db.execute(text("""
        SELECT scanner_host,
               DATE_FORMAT(recorded_at, '%%Y-%%m-%%d %%H:%%i:00') as minute,
               AVG(signal_dbm) as avg_signal,
               COUNT(*) as sample_count
        FROM observations
        WHERE mac = :mac AND recorded_at >= NOW() - INTERVAL 24 HOUR
        GROUP BY scanner_host, minute
        ORDER BY minute
    """), {"mac": mac})
    signal_history = [dict(r) for r in r4.mappings().all()]

    return {
        "device": dict(device),
        "ssids": ssids,
        "observations": observations,
        "signal_history": signal_history,
    }


@router.patch("/{mac}/classify")
async def classify_device(mac: str, body: dict, db: AsyncSession = Depends(get_db)):
    status = body.get("status")
    label = body.get("label")
    owner = body.get("owner")

    if status and status not in ("known", "unknown", "guest", "rogue"):
        return {"error": "invalid status"}, 400

    updates = []
    params = {"mac": mac}
    if status:
        updates.append("status = :status")
        params["status"] = status
    if label is not None:
        updates.append("label = :label")
        params["label"] = label
    if owner is not None:
        updates.append("owner = :owner")
        params["owner"] = owner

    if not updates:
        return {"error": "nothing to update"}, 400

    await db.execute(text(f"""
        INSERT INTO known_devices (mac, {', '.join(k.split(' = :')[0] for k in updates)}, synced_at)
        VALUES (:mac, {', '.join(':' + k.split(' = :')[1] for k in updates)}, NOW())
        ON DUPLICATE KEY UPDATE {', '.join(updates)}, synced_at = NOW()
    """), params)
    await db.commit()
    return {"ok": True}
