"""Route analysis for mobile scan sessions.

Computes route fingerprints (grid cells), matches similar routes via Jaccard
similarity, reverse-geocodes endpoints, and auto-names sessions.
"""
import asyncio
import json
import logging
import uuid
from datetime import datetime

import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

logger = logging.getLogger(__name__)

# Grid resolution for route fingerprinting (~100m cells)
GRID_RES = 0.001  # ~111m lat, ~80m lon at mid-latitudes
# Jaccard similarity threshold to consider two routes "the same"
SIMILARITY_THRESHOLD = 0.55


def _route_cells(points: list[tuple[float, float]]) -> list[str]:
    """Convert GPS points into a deduplicated set of grid cell keys."""
    cells = set()
    for lat, lon in points:
        gx = round(lat / GRID_RES)
        gy = round(lon / GRID_RES)
        cells.add(f"{gx},{gy}")
    return sorted(cells)


def _jaccard(a: list[str], b: list[str]) -> float:
    """Jaccard similarity between two cell sets."""
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    inter = len(sa & sb)
    union = len(sa | sb)
    return inter / union if union else 0.0


async def _reverse_geocode(lat: float, lon: float) -> str | None:
    """Reverse geocode a point using Nominatim (OpenStreetMap)."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                "https://nominatim.openstreetmap.org/reverse",
                params={"lat": lat, "lon": lon, "format": "json", "zoom": 16},
                headers={"User-Agent": "air-scan/1.0"},
            )
            r.raise_for_status()
            data = r.json()
            addr = data.get("address", {})
            # Build a short location string
            parts = []
            for key in ("road", "neighbourhood", "suburb", "city", "town", "village"):
                if key in addr:
                    parts.append(addr[key])
                    if len(parts) >= 2:
                        break
            return ", ".join(parts) if parts else data.get("display_name", "")[:80]
    except Exception as e:
        logger.warning("Reverse geocode failed for %.5f,%.5f: %s", lat, lon, e)
        return None


def _build_auto_name(start_addr: str | None, end_addr: str | None) -> str:
    """Generate an auto-name from start/end addresses."""
    if not start_addr and not end_addr:
        return "Unknown route"
    if start_addr and end_addr:
        if start_addr == end_addr:
            return f"Around {start_addr}"
        return f"{start_addr} → {end_addr}"
    return start_addr or end_addr or "Unknown route"


async def analyze_session(session_id: str, db: AsyncSession) -> dict | None:
    """Analyze a single session: compute route cells, geocode, find route group."""

    # Fetch GPS points ordered by time
    r = await db.execute(text("""
        SELECT gps_lat, gps_lon, recorded_at, mac
        FROM mobile_observations
        WHERE session_id = :sid AND gps_fix = 1
          AND gps_lat IS NOT NULL AND gps_lon IS NOT NULL
        ORDER BY recorded_at ASC
    """), {"sid": session_id})
    rows = r.mappings().all()

    if len(rows) < 5:
        logger.info("Session %s has only %d GPS points, skipping", session_id, len(rows))
        return None

    points = [(float(row["gps_lat"]), float(row["gps_lon"])) for row in rows]
    macs = {row["mac"] for row in rows}
    scanner_host_r = await db.execute(text(
        "SELECT scanner_host FROM mobile_observations WHERE session_id = :sid LIMIT 1"
    ), {"sid": session_id})
    scanner_host = scanner_host_r.scalar() or "unknown"

    # Compute route fingerprint
    cells = _route_cells(points)

    # Start and end points
    start_lat, start_lon = points[0]
    end_lat, end_lon = points[-1]

    # Reverse geocode start and end (with Nominatim rate limit: 1 req/sec)
    start_address = await _reverse_geocode(start_lat, start_lon)
    await asyncio.sleep(2)  # respect Nominatim rate limit (1 req/sec policy)
    end_address = await _reverse_geocode(end_lat, end_lon)

    auto_name = _build_auto_name(start_address, end_address)

    # Find best matching existing route group
    existing = await db.execute(text(
        "SELECT session_id, route_group, route_cells, custom_name FROM session_meta WHERE route_cells IS NOT NULL"
    ))
    best_match = None
    best_sim = 0.0
    for row in existing.mappings().all():
        if row["session_id"] == session_id:
            continue
        try:
            other_cells = json.loads(row["route_cells"]) if isinstance(row["route_cells"], str) else row["route_cells"]
        except (json.JSONDecodeError, TypeError):
            continue
        sim = _jaccard(cells, other_cells)
        if sim > best_sim:
            best_sim = sim
            best_match = row

    if best_match and best_sim >= SIMILARITY_THRESHOLD:
        route_group = best_match["route_group"]
        # Inherit custom name if the matching group has one
        if best_match["custom_name"]:
            today = datetime.now().strftime("%-m-%-d-%y")
            auto_name = f"{best_match['custom_name']} {today}"
    else:
        route_group = str(uuid.uuid4())[:12]

    # Upsert session_meta
    await db.execute(text("""
        INSERT INTO session_meta
            (session_id, scanner_host, auto_name, route_group, route_cells,
             start_lat, start_lon, end_lat, end_lon,
             start_address, end_address, obs_count, device_count, analyzed_at)
        VALUES
            (:sid, :host, :auto_name, :rg, :cells,
             :slat, :slon, :elat, :elon,
             :saddr, :eaddr, :obs, :dev, NOW())
        ON DUPLICATE KEY UPDATE
            auto_name = VALUES(auto_name),
            route_group = VALUES(route_group),
            route_cells = VALUES(route_cells),
            start_lat = VALUES(start_lat), start_lon = VALUES(start_lon),
            end_lat = VALUES(end_lat), end_lon = VALUES(end_lon),
            start_address = VALUES(start_address), end_address = VALUES(end_address),
            obs_count = VALUES(obs_count), device_count = VALUES(device_count),
            analyzed_at = NOW()
    """), {
        "sid": session_id, "host": scanner_host,
        "auto_name": auto_name, "rg": route_group,
        "cells": json.dumps(cells),
        "slat": start_lat, "slon": start_lon,
        "elat": end_lat, "elon": end_lon,
        "saddr": start_address, "eaddr": end_address,
        "obs": len(rows), "dev": len(macs),
    })
    await db.commit()

    return {
        "session_id": session_id,
        "auto_name": auto_name,
        "route_group": route_group,
        "similarity": round(best_sim, 3) if best_match else 0,
        "cells_count": len(cells),
        "obs_count": len(rows),
        "device_count": len(macs),
    }


async def analyze_all_unanalyzed(db: AsyncSession) -> list[dict]:
    """Find and analyze all sessions that haven't been analyzed yet."""
    r = await db.execute(text("""
        SELECT DISTINCT mo.session_id
        FROM mobile_observations mo
        LEFT JOIN session_meta sm ON sm.session_id = mo.session_id
        WHERE mo.session_id IS NOT NULL
          AND (sm.analyzed_at IS NULL
               OR sm.start_address IS NULL
               OR sm.analyzed_at < mo.recorded_at - INTERVAL 5 MINUTE)
        GROUP BY mo.session_id
        HAVING COUNT(*) >= 5
    """))
    session_ids = [row[0] for row in r.all()]

    results = []
    for i, sid in enumerate(session_ids):
        if i > 0:
            await asyncio.sleep(2)  # rate limit between sessions
        result = await analyze_session(sid, db)
        if result:
            results.append(result)

    return results
