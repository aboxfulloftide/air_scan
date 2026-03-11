from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from api.db import get_db

router = APIRouter(prefix="/api/maps", tags=["maps"])


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


@router.delete("/aps/{mac}")
async def remove_ap_placement(mac: str, db: AsyncSession = Depends(get_db)):
    """Remove an AP's manual placement from the map."""
    await db.execute(text(
        "DELETE FROM device_positions WHERE mac = :mac AND method = 'manual'"
    ), {"mac": mac})
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
