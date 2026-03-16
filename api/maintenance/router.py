from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from api.db import get_db
import asyncio

router = APIRouter(prefix="/api/maintenance", tags=["maintenance"])


@router.get("/settings")
async def get_settings(db: AsyncSession = Depends(get_db)):
    r = await db.execute(text("SELECT key_name, value FROM settings"))
    return {row.key_name: row.value for row in r.fetchall()}


@router.patch("/settings")
async def update_settings(body: dict, db: AsyncSession = Depends(get_db)):
    allowed = {
        "observation_retention_days",
        "triangulation_tx_power",
        "triangulation_path_loss_n",
        "triangulation_interval_seconds",
        "triangulation_window_seconds",
        "position_retention_days",
    }
    for key, value in body.items():
        if key not in allowed:
            continue
        await db.execute(
            text("INSERT INTO settings (key_name, value) VALUES (:k, :v) "
                 "ON DUPLICATE KEY UPDATE value = :v, updated_at = NOW()"),
            {"k": key, "v": str(value)}
        )
    await db.commit()
    return {"ok": True}


@router.post("/cleanup")
async def run_cleanup(db: AsyncSession = Depends(get_db)):
    """Delete observations older than the configured retention period."""
    r = await db.execute(
        text("SELECT value FROM settings WHERE key_name = 'observation_retention_days'")
    )
    row = r.fetchone()
    retention_days = int(row.value) if row else 3

    result = await db.execute(text(
        "DELETE FROM observations WHERE recorded_at < NOW() - INTERVAL :days DAY"
    ), {"days": retention_days})
    await db.commit()

    deleted = result.rowcount

    # Record last cleanup time and count
    await db.execute(text(
        "INSERT INTO settings (key_name, value) VALUES ('last_cleanup_at', NOW()) "
        "ON DUPLICATE KEY UPDATE value = NOW(), updated_at = NOW()"
    ))
    await db.execute(text(
        "INSERT INTO settings (key_name, value) VALUES ('last_cleanup_deleted', :n) "
        "ON DUPLICATE KEY UPDATE value = :n, updated_at = NOW()"
    ), {"n": str(deleted)})
    await db.commit()

    return {"deleted": deleted, "retention_days": retention_days}
