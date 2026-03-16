from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from api.db import get_db
import re

router = APIRouter(prefix="/api/maps", tags=["maps"])
MAC_RE = re.compile(r"^(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")


async def _resolve_fixed_anchor_targets(db: AsyncSession, mac: str, host_id: int | None):
    if host_id:
        r = await db.execute(text("""
            SELECT mac
            FROM known_devices
            WHERE port_scan_host_id = :host_id
        """), {"host_id": host_id})
        macs = [row.mac for row in r.all()]
        if macs:
            return macs
    return [mac]


@router.get("/config")
async def get_map_config(db: AsyncSession = Depends(get_db)):
    """Get map configuration (address/center point)."""
    r = await db.execute(text("SELECT * FROM map_config ORDER BY id LIMIT 1"))
    row = r.mappings().first()
    if not row:
        return None
    return dict(row)


@router.post("/config")
async def set_map_config(body: dict, db: AsyncSession = Depends(get_db)):
    """Set or update map configuration with address geocoded lat/lon."""
    label = body.get("label", "Home")
    lat = body.get("lat")
    lon = body.get("lon")
    floor = body.get("floor", 0)

    if lat is None or lon is None:
        return {"error": "lat and lon required"}, 400

    # Upsert — only one map config for now
    r = await db.execute(text("SELECT id FROM map_config LIMIT 1"))
    existing = r.scalar()

    if existing:
        await db.execute(text("""
            UPDATE map_config SET label = :label, gps_anchor_lat = :lat, gps_anchor_lon = :lon,
            floor = :floor WHERE id = :id
        """), {"label": label, "lat": lat, "lon": lon, "floor": floor, "id": existing})
    else:
        await db.execute(text("""
            INSERT INTO map_config (label, floor, gps_anchor_lat, gps_anchor_lon)
            VALUES (:label, :floor, :lat, :lon)
        """), {"label": label, "floor": floor, "lat": lat, "lon": lon})

    await db.commit()
    return {"ok": True}


@router.get("/zones")
async def list_zones(db: AsyncSession = Depends(get_db)):
    r = await db.execute(text("SELECT * FROM map_zones ORDER BY label"))
    return [dict(r) for r in r.mappings().all()]


@router.post("/zones")
async def create_zone(body: dict, db: AsyncSession = Depends(get_db)):
    r = await db.execute(text("SELECT id FROM map_config LIMIT 1"))
    map_id = r.scalar()
    if not map_id:
        return {"error": "set map config first"}, 400

    await db.execute(text("""
        INSERT INTO map_zones (map_id, label, polygon_json, zone_type)
        VALUES (:map_id, :label, :polygon, :zone_type)
    """), {
        "map_id": map_id,
        "label": body["label"],
        "polygon": str(body["polygon"]),
        "zone_type": body.get("zone_type", "common"),
    })
    await db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Computed positions (triangulation results)
# ---------------------------------------------------------------------------

@router.get("/positions")
async def get_computed_positions(db: AsyncSession = Depends(get_db)):
    """Latest computed position per MAC from the last 5 minutes.

    Excludes manual and fixed positions (those have /aps and /devices/fixed).
    Joins with device + known_device info for labels.
    """
    r = await db.execute(text("""
        SELECT dp.mac, dp.x_pos AS lat, dp.y_pos AS lon, dp.floor,
               dp.confidence, dp.method, dp.scanner_count, dp.computed_at,
               d.device_type, d.manufacturer, d.oui,
               kd.label AS known_label, kd.owner, kd.status,
               GROUP_CONCAT(DISTINCT s.ssid ORDER BY s.ssid SEPARATOR ', ') AS ssids
        FROM device_positions dp
        JOIN devices d ON d.mac = dp.mac
        LEFT JOIN known_devices kd ON kd.mac = dp.mac
        LEFT JOIN ssids s ON s.mac = dp.mac
        WHERE dp.method NOT IN ('manual', 'fixed')
          AND dp.computed_at >= NOW() - INTERVAL 5 MINUTE
          AND dp.id IN (
              SELECT MAX(id) FROM device_positions
              WHERE method NOT IN ('manual', 'fixed')
                AND computed_at >= NOW() - INTERVAL 5 MINUTE
              GROUP BY mac
          )
        GROUP BY dp.mac, dp.x_pos, dp.y_pos, dp.floor,
                 dp.confidence, dp.method, dp.scanner_count, dp.computed_at,
                 d.device_type, d.manufacturer, d.oui,
                 kd.label, kd.owner, kd.status
        ORDER BY dp.confidence DESC
    """))
    return [dict(row) for row in r.mappings().all()]


@router.get("/positions/{mac}")
async def get_device_position(mac: str, db: AsyncSession = Depends(get_db)):
    """Get the latest position for a specific device from any source."""
    r = await db.execute(text("""
        SELECT x_pos AS lat, y_pos AS lon, method, confidence, scanner_count, computed_at
        FROM device_positions
        WHERE mac = :mac
        ORDER BY computed_at DESC
        LIMIT 1
    """), {"mac": mac})
    row = r.mappings().first()
    if not row:
        return None
    return dict(row)


# ---------------------------------------------------------------------------
# AP placement on map
# ---------------------------------------------------------------------------

@router.get("/aps")
async def list_placed_aps(db: AsyncSession = Depends(get_db)):
    """List APs that have been manually placed on the map."""
    r = await db.execute(text("""
        SELECT dp.mac, dp.x_pos as lat, dp.y_pos as lon, dp.floor, dp.z_pos,
               d.device_type, d.manufacturer, d.oui,
               GROUP_CONCAT(DISTINCT s.ssid ORDER BY s.ssid SEPARATOR ', ') as ssids
        FROM device_positions dp
        JOIN devices d ON d.mac = dp.mac
        LEFT JOIN ssids s ON s.mac = dp.mac
        WHERE dp.method = 'manual'
        AND dp.id IN (
            SELECT MAX(id) FROM device_positions
            WHERE method = 'manual'
            GROUP BY mac
        )
        GROUP BY dp.mac, dp.x_pos, dp.y_pos, dp.floor, dp.z_pos,
                 d.device_type, d.manufacturer, d.oui
    """))
    return [dict(row) for row in r.mappings().all()]


@router.get("/devices/fixed")
async def list_fixed_devices(db: AsyncSession = Depends(get_db)):
    """List fixed-position client devices that have been pinned on the map."""
    r = await db.execute(text("""
        SELECT MIN(kd.mac) as mac,
               MAX(kd.port_scan_host_id) as port_scan_host_id,
               MAX(kd.label) as label,
               MAX(kd.owner) as owner,
               MAX(kd.status) as status,
               MAX(kd.fixed_x) as lat,
               MAX(kd.fixed_y) as lon,
               MAX(kd.fixed_z) as z_pos,
               MAX(kd.fixed_floor) as floor,
               MAX(d.device_type) as device_type,
               MAX(d.manufacturer) as manufacturer,
               MAX(d.last_seen) as last_seen,
               GROUP_CONCAT(DISTINCT s.ssid ORDER BY s.ssid SEPARATOR ', ') as ssids
        FROM known_devices kd
        JOIN devices d ON d.mac = kd.mac
        LEFT JOIN ssids s ON s.mac = kd.mac
        WHERE kd.is_fixed = TRUE
        GROUP BY COALESCE(CONCAT('host:', kd.port_scan_host_id), CONCAT('mac:', kd.mac))
        ORDER BY COALESCE(MAX(kd.label), MAX(d.manufacturer), MIN(kd.mac))
    """))
    return [dict(row) for row in r.mappings().all()]


@router.get("/aps/search")
async def search_aps(q: str = "", db: AsyncSession = Depends(get_db)):
    """Search APs by MAC, manufacturer, or SSID for placement."""
    r = await db.execute(text("""
        SELECT d.mac, d.manufacturer, d.oui,
               GROUP_CONCAT(DISTINCT s.ssid ORDER BY s.ssid SEPARATOR ', ') as ssids
        FROM devices d
        LEFT JOIN ssids s ON s.mac = d.mac
        WHERE d.device_type = 'AP'
        AND (d.mac LIKE :q OR d.manufacturer LIKE :q OR s.ssid LIKE :q)
        GROUP BY d.mac, d.manufacturer, d.oui
        ORDER BY d.last_seen DESC
        LIMIT 20
    """), {"q": f"%{q}%"})
    return [dict(row) for row in r.mappings().all()]


@router.get("/devices/search")
async def search_devices(q: str = "", db: AsyncSession = Depends(get_db)):
    """Search devices that can be pinned as fixed map anchors."""
    normalized_q = q.strip()
    if len(normalized_q) < 2:
        return []

    if MAC_RE.fullmatch(normalized_q):
        r = await db.execute(text("""
            SELECT d.mac, d.device_type, d.manufacturer, d.last_seen,
                   kd.port_scan_host_id, kd.label as known_label, kd.owner, kd.status, kd.is_fixed,
                   GROUP_CONCAT(DISTINCT s.ssid ORDER BY s.ssid SEPARATOR ', ') as ssids
            FROM devices d
            LEFT JOIN known_devices kd ON kd.mac = d.mac
            LEFT JOIN ssids s ON s.mac = d.mac
            WHERE d.mac = :mac
            GROUP BY d.mac, d.device_type, d.manufacturer, d.last_seen,
                     kd.port_scan_host_id, kd.label, kd.owner, kd.status, kd.is_fixed
            ORDER BY d.last_seen DESC
            LIMIT 20
        """), {"mac": normalized_q.lower()})
    else:
        r = await db.execute(text("""
            SELECT d.mac, d.device_type, d.manufacturer, d.last_seen,
                   kd.port_scan_host_id, kd.label as known_label, kd.owner, kd.status, kd.is_fixed,
                   GROUP_CONCAT(DISTINCT s.ssid ORDER BY s.ssid SEPARATOR ', ') as ssids
            FROM devices d
            LEFT JOIN known_devices kd ON kd.mac = d.mac
            LEFT JOIN ssids s ON s.mac = d.mac
            WHERE (
                d.mac LIKE :q OR
                d.manufacturer LIKE :q OR
                s.ssid LIKE :q OR
                kd.label LIKE :q OR
                kd.owner LIKE :q OR
                EXISTS (
                    SELECT 1
                    FROM known_devices kd2
                    WHERE kd2.port_scan_host_id = kd.port_scan_host_id
                      AND (kd2.label LIKE :q OR kd2.owner LIKE :q)
                )
            )
            GROUP BY d.mac, d.device_type, d.manufacturer, d.last_seen,
                     kd.port_scan_host_id, kd.label, kd.owner, kd.status, kd.is_fixed
            ORDER BY
                COALESCE(kd.is_fixed, FALSE) DESC,
                COALESCE(kd.label, '') DESC,
                d.last_seen DESC
            LIMIT 20
        """), {"q": f"%{normalized_q}%"})
    return [dict(row) for row in r.mappings().all()]


@router.post("/aps/place")
async def place_ap(body: dict, db: AsyncSession = Depends(get_db)):
    """Place an AP on the map at a lat/lon with optional height offset in feet."""
    mac = body.get("mac")
    lat = body.get("lat")
    lon = body.get("lon")
    z = body.get("z_pos", 0)
    floor = body.get("floor", 0)

    if not mac or lat is None or lon is None:
        return {"error": "mac, lat, and lon required"}, 400

    # Remove any previous manual placement for this MAC
    await db.execute(text(
        "DELETE FROM device_positions WHERE mac = :mac AND method = 'manual'"
    ), {"mac": mac})

    await db.execute(text("""
        INSERT INTO device_positions (mac, x_pos, y_pos, z_pos, floor, confidence, method, scanner_count, computed_at)
        VALUES (:mac, :lat, :lon, :z, :floor, 100.0, 'manual', 0, NOW())
    """), {"mac": mac, "lat": lat, "lon": lon, "z": z, "floor": floor})

    await db.commit()
    return {"ok": True}


@router.post("/devices/fixed")
async def place_fixed_device(body: dict, db: AsyncSession = Depends(get_db)):
    """Pin a device to a fixed position on the map for calibration and reference."""
    mac = body.get("mac")
    lat = body.get("lat")
    lon = body.get("lon")
    z = body.get("z_pos", 0)
    floor = body.get("floor", 0)
    host_id = body.get("port_scan_host_id")

    if not mac or lat is None or lon is None:
        return {"error": "mac, lat, and lon required"}, 400

    target_macs = await _resolve_fixed_anchor_targets(db, mac, host_id)

    for target_mac in target_macs:
        await db.execute(text("""
            INSERT INTO known_devices (mac, is_fixed, fixed_x, fixed_y, fixed_z, fixed_floor, synced_at)
            VALUES (:mac, TRUE, :lat, :lon, :z, :floor, NOW())
            ON DUPLICATE KEY UPDATE
                is_fixed = TRUE,
                fixed_x = :lat,
                fixed_y = :lon,
                fixed_z = :z,
                fixed_floor = :floor,
                synced_at = NOW()
        """), {"mac": target_mac, "lat": lat, "lon": lon, "z": z, "floor": floor})

        await db.execute(text(
            "DELETE FROM device_positions WHERE mac = :mac AND method = 'fixed'"
        ), {"mac": target_mac})

        await db.execute(text("""
            INSERT INTO device_positions (mac, x_pos, y_pos, z_pos, floor, confidence, method, scanner_count, computed_at)
            VALUES (:mac, :lat, :lon, :z, :floor, 100.0, 'fixed', 0, NOW())
        """), {"mac": target_mac, "lat": lat, "lon": lon, "z": z, "floor": floor})

    await db.commit()
    return {"ok": True}


@router.delete("/aps/{mac}")
async def remove_ap_placement(mac: str, db: AsyncSession = Depends(get_db)):
    """Remove an AP's manual placement from the map."""
    await db.execute(text(
        "DELETE FROM device_positions WHERE mac = :mac AND method = 'manual'"
    ), {"mac": mac})
    await db.commit()
    return {"ok": True}


@router.delete("/devices/fixed/{mac}")
async def remove_fixed_device(mac: str, db: AsyncSession = Depends(get_db)):
    """Remove a device's fixed map pin."""
    r = await db.execute(text("""
        SELECT port_scan_host_id
        FROM known_devices
        WHERE mac = :mac
    """), {"mac": mac})
    host_id = r.scalar()

    target_macs = await _resolve_fixed_anchor_targets(db, mac, host_id)

    for target_mac in target_macs:
        await db.execute(text("""
            UPDATE known_devices
            SET is_fixed = FALSE, fixed_x = NULL, fixed_y = NULL, fixed_z = NULL, fixed_floor = 0, synced_at = NOW()
            WHERE mac = :mac
        """), {"mac": target_mac})
        await db.execute(text(
            "DELETE FROM device_positions WHERE mac = :mac AND method = 'fixed'"
        ), {"mac": target_mac})
    await db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Walls
# ---------------------------------------------------------------------------

WALL_ATTENUATION = {
    "exterior": 8.0,
    "interior": 3.0,
    "addition": 6.0,
}


@router.get("/walls")
async def list_walls(db: AsyncSession = Depends(get_db)):
    r = await db.execute(text("SELECT * FROM map_walls ORDER BY id"))
    return [dict(row) for row in r.mappings().all()]


@router.post("/walls")
async def create_wall(body: dict, db: AsyncSession = Depends(get_db)):
    wall_type = body.get("wall_type", "interior")
    points = body.get("points")
    label = body.get("label", "")
    attenuation = body.get("attenuation_db", WALL_ATTENUATION.get(wall_type, 3.0))

    if not points or len(points) < 2:
        return {"error": "need at least 2 points"}, 400

    r = await db.execute(text("SELECT id FROM map_config LIMIT 1"))
    map_id = r.scalar()

    result = await db.execute(text("""
        INSERT INTO map_walls (map_id, wall_type, points_json, attenuation_db, label)
        VALUES (:map_id, :wall_type, :points, :attenuation, :label)
    """), {
        "map_id": map_id,
        "wall_type": wall_type,
        "points": str(points).replace("'", '"'),
        "attenuation": attenuation,
        "label": label,
    })
    await db.commit()
    return {"ok": True, "id": result.lastrowid}


@router.delete("/walls/{wall_id}")
async def delete_wall(wall_id: int, db: AsyncSession = Depends(get_db)):
    await db.execute(text("DELETE FROM map_walls WHERE id = :id"), {"id": wall_id})
    await db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Floor zones
# ---------------------------------------------------------------------------

@router.get("/floors")
async def list_floors(db: AsyncSession = Depends(get_db)):
    r = await db.execute(text("SELECT * FROM map_floors ORDER BY id"))
    return [dict(row) for row in r.mappings().all()]


@router.post("/floors")
async def create_floor(body: dict, db: AsyncSession = Depends(get_db)):
    floor_type = body.get("floor_type", "hardwood")
    polygon = body.get("polygon")
    label = body.get("label", "")

    if not polygon or len(polygon) < 3:
        return {"error": "need at least 3 points for a polygon"}, 400

    r = await db.execute(text("SELECT id FROM map_config LIMIT 1"))
    map_id = r.scalar()

    result = await db.execute(text("""
        INSERT INTO map_floors (map_id, floor_type, polygon_json, label)
        VALUES (:map_id, :floor_type, :polygon, :label)
    """), {
        "map_id": map_id,
        "floor_type": floor_type,
        "polygon": str(polygon).replace("'", '"'),
        "label": label,
    })
    await db.commit()
    return {"ok": True, "id": result.lastrowid}


@router.delete("/floors/{floor_id}")
async def delete_floor(floor_id: int, db: AsyncSession = Depends(get_db)):
    await db.execute(text("DELETE FROM map_floors WHERE id = :id"), {"id": floor_id})
    await db.commit()
    return {"ok": True}
