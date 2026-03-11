from fastapi import APIRouter, Depends
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
            (SELECT COUNT(*) FROM observations o
             WHERE o.scanner_host = s.hostname
             AND o.recorded_at >= NOW() - INTERVAL 10 MINUTE) as recent_obs
        FROM scanners s
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
