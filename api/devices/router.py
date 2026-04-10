from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from api.db import get_db
import re

router = APIRouter(prefix="/api/devices", tags=["devices"])

MAC_RE = re.compile(r"^(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")


@router.get("/")
async def list_devices(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    device_type: str = Query(None),
    status: str = Query(None),
    mapped: str = Query(None),
    search: str = Query(None),
    sort: str = Query("last_seen"),
    order: str = Query("desc"),
    db: AsyncSession = Depends(get_db),
):
    allowed_sorts = {"last_seen", "first_seen", "mac", "manufacturer", "probes"}
    if sort not in allowed_sorts:
        sort = "last_seen"
    if order not in ("asc", "desc"):
        order = "desc"

    conditions = []
    params = {}
    exact_mac = None

    if device_type in ("AP", "Client"):
        conditions.append("d.device_type = :device_type")
        params["device_type"] = device_type

    if status in ("known", "unknown", "guest", "rogue"):
        conditions.append("kd.status = :status")
        params["status"] = status

    if mapped == "yes":
        conditions.append("EXISTS (SELECT 1 FROM device_positions dp WHERE dp.mac = d.mac)")
    elif mapped == "no":
        conditions.append("NOT EXISTS (SELECT 1 FROM device_positions dp WHERE dp.mac = d.mac)")

    if search:
        normalized_search = search.strip()
        if MAC_RE.fullmatch(normalized_search):
            exact_mac = normalized_search.lower()
            conditions.append("d.mac = :exact_mac")
            params["exact_mac"] = exact_mac
        else:
            conditions.append("""
                (
                    d.mac LIKE :search OR
                    d.manufacturer LIKE :search OR
                    s.ssid LIKE :search OR
                    kd.label LIKE :search OR
                    kd.owner LIKE :search OR
                    EXISTS (
                        SELECT 1
                        FROM known_devices kd2
                        WHERE kd2.port_scan_host_id = kd.port_scan_host_id
                        AND (kd2.label LIKE :search OR kd2.owner LIKE :search)
                    )
                )
            """)
            params["search"] = f"%{normalized_search}%"

    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    offset = (page - 1) * per_page

    # Count
    count_sql = (
        "SELECT COUNT(*) FROM devices d"
        if not search and not status and not mapped
        else f"""
            SELECT COUNT(DISTINCT d.mac)
            FROM devices d
            LEFT JOIN known_devices kd ON d.mac = kd.mac
            LEFT JOIN ssids s ON d.mac = s.mac
            {where}
        """
    )
    r = await db.execute(text(count_sql), params)
    total = r.scalar()

    sort_col = {
        "last_seen": "d.last_seen",
        "first_seen": "d.first_seen",
        "mac": "d.mac",
        "manufacturer": "d.manufacturer",
        "probes": "COALESCE(probe_agg.probe_count, 0)",
    }[sort]

    base_select = f"""
        SELECT d.mac, d.device_type, d.oui, d.manufacturer, d.is_randomized,
               d.ht_capable, d.vht_capable, d.he_capable,
               d.first_seen, d.last_seen,
               kd.status as known_status,
               COALESCE(kd.label, host_label.label) as known_label,
               kd.owner,
               kd.is_fixed,
               COALESCE(probe_agg.probe_count, 0) AS probe_count
        FROM devices d
        LEFT JOIN known_devices kd ON d.mac = kd.mac
        LEFT JOIN (
            SELECT port_scan_host_id, MAX(label) AS label
            FROM known_devices
            WHERE port_scan_host_id IS NOT NULL
              AND label IS NOT NULL AND label != ''
            GROUP BY port_scan_host_id
        ) host_label ON host_label.port_scan_host_id = kd.port_scan_host_id
            AND kd.label IS NULL
        LEFT JOIN (
            SELECT mac, SUM(probe_count) AS probe_count
            FROM observations
            WHERE recorded_at >= UTC_TIMESTAMP() - INTERVAL 10 MINUTE
            GROUP BY mac
        ) probe_agg ON probe_agg.mac = d.mac
    """
    data_sql = (
        base_select + f"""
        {where}
        ORDER BY {sort_col} {order}
        LIMIT :limit OFFSET :offset
        """
        if not search or exact_mac
        else base_select + f"""
        LEFT JOIN ssids s ON d.mac = s.mac
        {where}
        GROUP BY d.mac, d.device_type, d.oui, d.manufacturer, d.is_randomized,
                 d.ht_capable, d.vht_capable, d.he_capable,
                 d.first_seen, d.last_seen,
                 kd.status, kd.label, kd.owner, kd.is_fixed, kd.port_scan_host_id,
                 host_label.label, probe_agg.probe_count
        ORDER BY {sort_col} {order}
        LIMIT :limit OFFSET :offset
        """
    )
    params["limit"] = per_page
    params["offset"] = offset

    r = await db.execute(text(data_sql), params)
    devices = [dict(row) for row in r.mappings().all()]

    macs = [d["mac"] for d in devices]

    # Check which devices have a position (computed, manual, or fixed)
    pos_by_mac = {}
    if macs:
        pos_params = {f"mac_{i}": mac for i, mac in enumerate(macs)}
        placeholders = ", ".join(f":mac_{i}" for i in range(len(macs)))
        rp = await db.execute(text(f"""
            SELECT dp.mac, dp.x_pos AS lat, dp.y_pos AS lon, dp.method, dp.confidence
            FROM device_positions dp
            INNER JOIN (
                SELECT mac, MAX(id) AS max_id
                FROM device_positions
                WHERE mac IN ({placeholders})
                GROUP BY mac
            ) latest ON dp.id = latest.max_id
        """), pos_params)
        pos_by_mac = {row["mac"]: dict(row) for row in rp.mappings().all()}

    ssids_by_mac = {}
    if macs:
        ssid_params = {f"mac_{i}": mac for i, mac in enumerate(macs)}
        placeholders = ", ".join(f":mac_{i}" for i in range(len(macs)))
        r2 = await db.execute(text(f"""
            SELECT mac, GROUP_CONCAT(DISTINCT ssid ORDER BY ssid SEPARATOR ', ') as ssids
            FROM ssids
            WHERE mac IN ({placeholders})
              AND ssid REGEXP '^[[:print:]]+$' AND CHAR_LENGTH(ssid) BETWEEN 1 AND 32
            GROUP BY mac
        """), ssid_params)
        ssids_by_mac = {row["mac"]: row["ssids"] for row in r2.mappings().all()}

    for device in devices:
        device["ssids"] = ssids_by_mac.get(device["mac"])
        device["probes_per_min"] = round(float(device.pop("probe_count", 0)) / 10.0, 1)
        pos = pos_by_mac.get(device["mac"])
        device["has_position"] = pos is not None
        if pos:
            device["position_lat"] = pos["lat"]
            device["position_lon"] = pos["lon"]
            device["position_method"] = pos["method"]

    return {"total": total, "page": page, "per_page": per_page, "devices": devices}


@router.get("/{mac}")
async def get_device(mac: str, db: AsyncSession = Depends(get_db)):
    # Device info
    r = await db.execute(text("""
        SELECT d.*, kd.status as known_status, kd.label as known_label,
               kd.owner, kd.port_scan_host_id, kd.is_fixed, kd.fixed_x, kd.fixed_y, kd.fixed_z, kd.fixed_floor
        FROM devices d
        LEFT JOIN known_devices kd ON d.mac = kd.mac
        WHERE d.mac = :mac
    """), {"mac": mac})
    device = r.mappings().first()
    if not device:
        return {"error": "not found"}, 404

    # SSIDs
    r2 = await db.execute(text(
        "SELECT ssid, first_seen FROM ssids WHERE mac = :mac AND ssid REGEXP '^[[:print:]]+$' AND CHAR_LENGTH(ssid) BETWEEN 1 AND 32 ORDER BY first_seen"
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

    # Probe frequency: total raw probes per scanner over last 10 minutes
    r5 = await db.execute(text("""
        SELECT scanner_host,
               SUM(probe_count) AS total_probes,
               ROUND(SUM(probe_count) / 10.0, 1) AS probes_per_min
        FROM observations
        WHERE mac = :mac AND recorded_at >= UTC_TIMESTAMP() - INTERVAL 10 MINUTE
        GROUP BY scanner_host
    """), {"mac": mac})
    probe_rate = {row["scanner_host"]: round(float(row["probes_per_min"]), 1)
                  for row in r5.mappings().all()}
    # Total across all scanners
    r5b = await db.execute(text("""
        SELECT ROUND(SUM(probe_count) / 10.0, 1) AS probes_per_min
        FROM observations
        WHERE mac = :mac AND recorded_at >= UTC_TIMESTAMP() - INTERVAL 10 MINUTE
    """), {"mac": mac})
    total_probes = round(float(r5b.scalar() or 0), 1)

    return {
        "device": dict(device),
        "ssids": ssids,
        "observations": observations,
        "signal_history": signal_history,
        "probe_rate": {"total": total_probes, "per_scanner": probe_rate},
    }


@router.patch("/{mac}/classify")
async def classify_device(mac: str, body: dict, db: AsyncSession = Depends(get_db)):
    status = body.get("status")
    label = body.get("label")
    owner = body.get("owner")
    is_fixed = body.get("is_fixed")
    fixed_x = body.get("fixed_x")
    fixed_y = body.get("fixed_y")
    fixed_z = body.get("fixed_z")
    fixed_floor = body.get("fixed_floor")

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
    if is_fixed is not None:
        updates.append("is_fixed = :is_fixed")
        params["is_fixed"] = bool(is_fixed)
    if fixed_x is not None:
        updates.append("fixed_x = :fixed_x")
        params["fixed_x"] = fixed_x
    if fixed_y is not None:
        updates.append("fixed_y = :fixed_y")
        params["fixed_y"] = fixed_y
    if fixed_z is not None:
        updates.append("fixed_z = :fixed_z")
        params["fixed_z"] = fixed_z
    if fixed_floor is not None:
        updates.append("fixed_floor = :fixed_floor")
        params["fixed_floor"] = fixed_floor

    if not updates:
        return {"error": "nothing to update"}, 400

    await db.execute(text(f"""
        INSERT INTO known_devices (mac, {', '.join(k.split(' = :')[0] for k in updates)}, synced_at)
        VALUES (:mac, {', '.join(':' + k.split(' = :')[1] for k in updates)}, NOW())
        ON DUPLICATE KEY UPDATE {', '.join(updates)}, synced_at = NOW()
    """), params)
    await db.commit()
    return {"ok": True}
