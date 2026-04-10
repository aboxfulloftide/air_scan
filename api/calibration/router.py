from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from api.db import get_db

router = APIRouter(prefix="/api/calibration", tags=["calibration"])


@router.get("/points")
async def list_points(db: AsyncSession = Depends(get_db)):
    """List all calibration points with their per-scanner readings."""
    r = await db.execute(text("""
        SELECT cp.id, cp.mac, cp.lat, cp.lon, cp.floor, cp.label, cp.captured_at,
               d.manufacturer, d.device_type,
               kd.label AS known_label, kd.owner
        FROM calibration_points cp
        LEFT JOIN devices d ON d.mac = cp.mac
        LEFT JOIN known_devices kd ON kd.mac = cp.mac
        ORDER BY cp.captured_at DESC
    """))
    points = [dict(row) for row in r.mappings().all()]

    if not points:
        return []

    point_ids = [p["id"] for p in points]
    placeholders = ",".join([":p" + str(i) for i in range(len(point_ids))])
    params = {f"p{i}": pid for i, pid in enumerate(point_ids)}

    r = await db.execute(text(f"""
        SELECT cr.point_id, cr.scanner_host, cr.avg_rssi, cr.sample_count
        FROM calibration_readings cr
        WHERE cr.point_id IN ({placeholders})
        ORDER BY cr.scanner_host
    """), params)
    readings_by_point = {}
    for row in r.mappings().all():
        pid = row["point_id"]
        if pid not in readings_by_point:
            readings_by_point[pid] = []
        readings_by_point[pid].append({
            "scanner_host": row["scanner_host"],
            "avg_rssi": float(row["avg_rssi"]),
            "sample_count": row["sample_count"],
        })

    for p in points:
        p["readings"] = readings_by_point.get(p["id"], [])

    return points


@router.post("/capture")
async def capture_point(body: dict, db: AsyncSession = Depends(get_db)):
    """Capture a calibration point.

    Takes a MAC address and GPS coordinates, then grabs recent RSSI
    observations from all scanners to create a calibration reference.

    Body: {mac, lat, lon, floor?, label?, window_seconds?}
    """
    mac = body.get("mac")
    lat = body.get("lat")
    lon = body.get("lon")
    floor = body.get("floor", 0)
    label = body.get("label", "")
    window = body.get("window_seconds", 30)

    if not mac or lat is None or lon is None:
        return {"error": "mac, lat, and lon required"}, 400

    # Get active scanner hostnames
    r = await db.execute(text("""
        SELECT hostname FROM scanners
        WHERE x_pos IS NOT NULL AND y_pos IS NOT NULL AND is_active = TRUE
    """))
    scanner_hosts = [row.hostname for row in r.all()]

    if not scanner_hosts:
        return {"error": "No positioned scanners found"}, 400

    # Query recent observations for this MAC from each scanner
    placeholders = ",".join([":s" + str(i) for i in range(len(scanner_hosts))])
    params = {f"s{i}": h for i, h in enumerate(scanner_hosts)}
    params["mac"] = mac
    params["window"] = window

    r = await db.execute(text(f"""
        SELECT scanner_host, AVG(signal_dbm) AS avg_rssi, COUNT(*) AS sample_count
        FROM observations
        WHERE mac = :mac
          AND signal_dbm IS NOT NULL
          AND scanner_host IN ({placeholders})
          AND recorded_at >= NOW() - INTERVAL :window SECOND
        GROUP BY scanner_host
    """), params)
    readings = [dict(row) for row in r.mappings().all()]

    if not readings:
        return {"error": f"No observations found for {mac} in the last {window}s"}, 404

    # Insert the calibration point
    result = await db.execute(text("""
        INSERT INTO calibration_points (mac, lat, lon, floor, label, captured_at)
        VALUES (:mac, :lat, :lon, :floor, :label, NOW())
    """), {"mac": mac, "lat": lat, "lon": lon, "floor": floor, "label": label})
    point_id = result.lastrowid

    # Insert per-scanner readings
    for reading in readings:
        await db.execute(text("""
            INSERT INTO calibration_readings (point_id, scanner_host, avg_rssi, sample_count)
            VALUES (:point_id, :scanner_host, :avg_rssi, :sample_count)
        """), {
            "point_id": point_id,
            "scanner_host": reading["scanner_host"],
            "avg_rssi": reading["avg_rssi"],
            "sample_count": reading["sample_count"],
        })

    await db.commit()
    return {
        "ok": True,
        "point_id": point_id,
        "readings": readings,
        "scanner_count": len(readings),
    }


@router.delete("/points/{point_id}")
async def delete_point(point_id: int, db: AsyncSession = Depends(get_db)):
    """Delete a calibration point and its readings (cascade)."""
    await db.execute(text("DELETE FROM calibration_points WHERE id = :id"), {"id": point_id})
    await db.commit()
    return {"ok": True}


@router.delete("/points")
async def delete_all_points(db: AsyncSession = Depends(get_db)):
    """Delete all calibration data."""
    await db.execute(text("DELETE FROM calibration_points"))
    await db.commit()
    return {"ok": True}


@router.get("/summary")
async def calibration_summary(db: AsyncSession = Depends(get_db)):
    """Summary stats for current calibration data — point count, scanner coverage, fit quality."""
    r = await db.execute(text("SELECT COUNT(*) AS cnt FROM calibration_points"))
    point_count = r.scalar()

    if point_count == 0:
        return {"point_count": 0, "scanner_coverage": {}, "fit": None}

    # Per-scanner reading count
    r = await db.execute(text("""
        SELECT cr.scanner_host, COUNT(*) AS reading_count
        FROM calibration_readings cr
        GROUP BY cr.scanner_host
    """))
    coverage = {row.scanner_host: row.reading_count for row in r.all()}

    # Compute path-loss fit from calibration data + scanner positions
    r = await db.execute(text("""
        SELECT cp.lat, cp.lon, cr.scanner_host, cr.avg_rssi,
               s.x_pos AS s_lat, s.y_pos AS s_lon
        FROM calibration_points cp
        JOIN calibration_readings cr ON cr.point_id = cp.id
        JOIN scanners s ON s.hostname = cr.scanner_host
        WHERE s.x_pos IS NOT NULL AND s.y_pos IS NOT NULL
    """))
    rows = r.mappings().all()

    fit = None
    if len(rows) >= 4:
        import math
        xs, ys = [], []
        for row in rows:
            lat1, lon1 = float(row["lat"]), float(row["lon"])
            lat2, lon2 = float(row["s_lat"]), float(row["s_lon"])
            rlat1, rlon1, rlat2, rlon2 = map(math.radians, (lat1, lon1, lat2, lon2))
            dlat = rlat2 - rlat1
            dlon = rlon2 - rlon1
            a = math.sin(dlat/2)**2 + math.cos(rlat1)*math.cos(rlat2)*math.sin(dlon/2)**2
            d = 6_371_000 * 2 * math.asin(math.sqrt(a))
            if d < 0.5:
                d = 0.5
            xs.append(math.log10(d))
            ys.append(float(row["avg_rssi"]))

        n = len(xs)
        sx = sum(xs)
        sy = sum(ys)
        sxy = sum(x*y for x, y in zip(xs, ys))
        sxx = sum(x*x for x in xs)
        denom = n * sxx - sx * sx

        if abs(denom) > 1e-10:
            b = (n * sxy - sx * sy) / denom
            a = (sy - b * sx) / n
            tx_power = round(a, 1)
            path_loss_n = round(-b / 10, 2)

            # R-squared
            y_mean = sy / n
            ss_tot = sum((y - y_mean)**2 for y in ys)
            ss_res = sum((y - (a + b*x))**2 for x, y in zip(xs, ys))
            r_squared = round(1 - ss_res / ss_tot, 3) if ss_tot > 0 else 0

            fit = {
                "tx_power": tx_power,
                "path_loss_n": path_loss_n,
                "r_squared": r_squared,
                "sample_count": n,
                "reasonable": 1.5 <= path_loss_n <= 6.0 and -60 <= tx_power <= -20,
            }

    return {"point_count": point_count, "scanner_coverage": coverage, "fit": fit}
