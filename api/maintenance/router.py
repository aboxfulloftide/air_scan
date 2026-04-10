from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from api.db import get_db
import logging

router = APIRouter(prefix="/api/maintenance", tags=["maintenance"])
logger = logging.getLogger(__name__)


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


@router.post("/partitions")
async def ensure_partitions(db: AsyncSession = Depends(get_db)):
    """Ensure observation partitions exist for the next 7 days.

    Safe to call frequently — skips partitions that already exist.
    Should run on its own schedule (e.g. every 6 hours) independent of cleanup.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # Get existing partition names
    r = await db.execute(text("""
        SELECT PARTITION_NAME
        FROM INFORMATION_SCHEMA.PARTITIONS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = 'observations'
          AND PARTITION_NAME IS NOT NULL
    """))
    existing = {row.PARTITION_NAME for row in r.fetchall()}

    added = []
    for i in range(7):
        day = (now + timedelta(days=i)).date()
        name = f"p{day.strftime('%Y%m%d')}"
        if name not in existing:
            next_day = day + timedelta(days=1)
            try:
                await db.execute(text(
                    f"ALTER TABLE observations REORGANIZE PARTITION p_future INTO ("
                    f"  PARTITION {name} VALUES LESS THAN (TO_DAYS('{next_day}')), "
                    f"  PARTITION p_future VALUES LESS THAN MAXVALUE)"
                ))
                await db.commit()
                added.append(name)
                logger.info("Added partition %s", name)
            except Exception as e:
                logger.warning("Could not add partition %s: %s", name, e)

    return {"added_partitions": added, "existing_count": len(existing)}


@router.post("/cleanup")
async def run_cleanup(db: AsyncSession = Depends(get_db)):
    """Drop old observation partitions and delete stale rows from p_future.

    Uses ALTER TABLE DROP PARTITION for instant cleanup of daily partitions.
    Also does a batched DELETE on p_future to catch any rows that landed there
    before a proper partition existed.
    """
    r = await db.execute(
        text("SELECT value FROM settings WHERE key_name = 'observation_retention_days'")
    )
    row = r.fetchone()
    retention_days = int(row.value) if row else 3

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    cutoff = now - timedelta(days=retention_days)
    cutoff_days = (cutoff.date() - datetime(1, 1, 1).date()).days + 366

    # Get existing partitions
    r = await db.execute(text("""
        SELECT PARTITION_NAME, PARTITION_DESCRIPTION
        FROM INFORMATION_SCHEMA.PARTITIONS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = 'observations'
          AND PARTITION_NAME IS NOT NULL
        ORDER BY PARTITION_ORDINAL_POSITION
    """))
    partitions = [(row.PARTITION_NAME, row.PARTITION_DESCRIPTION) for row in r.fetchall()]

    # Drop partitions older than retention period
    dropped = []
    for name, desc in partitions:
        if name == 'p_future' or desc == 'MAXVALUE':
            continue
        try:
            part_day_num = int(desc)
            if part_day_num < cutoff_days:
                await db.execute(text(f"ALTER TABLE observations DROP PARTITION {name}"))
                await db.commit()
                dropped.append(name)
                logger.info("Dropped partition %s", name)
        except (ValueError, Exception) as e:
            logger.warning("Could not process partition %s (%s): %s", name, desc, e)

    # Delete old rows from p_future in batches (catches data that landed
    # before a proper daily partition was created)
    future_deleted = 0
    batch_size = 10000
    while True:
        result = await db.execute(text(
            "DELETE FROM observations PARTITION (p_future) "
            "WHERE recorded_at < UTC_TIMESTAMP() - INTERVAL :days DAY "
            "LIMIT :batch"
        ), {"days": retention_days, "batch": batch_size})
        await db.commit()
        batch_deleted = result.rowcount
        future_deleted += batch_deleted
        if batch_deleted < batch_size:
            break

    # Clean up old device positions
    r2 = await db.execute(
        text("SELECT value FROM settings WHERE key_name = 'position_retention_days'")
    )
    row2 = r2.fetchone()
    position_days = int(row2.value) if row2 else 1
    pos_result = await db.execute(text(
        "DELETE FROM device_positions "
        "WHERE computed_at < UTC_TIMESTAMP() - INTERVAL :days DAY"
    ), {"days": position_days})
    await db.commit()

    # Record last cleanup
    await db.execute(text(
        "INSERT INTO settings (key_name, value) VALUES ('last_cleanup_at', UTC_TIMESTAMP()) "
        "ON DUPLICATE KEY UPDATE value = UTC_TIMESTAMP(), updated_at = NOW()"
    ))
    summary = f"{len(dropped)} partitions, {future_deleted} overflow rows"
    await db.execute(text(
        "INSERT INTO settings (key_name, value) VALUES ('last_cleanup_deleted', :n) "
        "ON DUPLICATE KEY UPDATE value = :n, updated_at = NOW()"
    ), {"n": summary})
    await db.commit()

    return {
        "dropped_partitions": dropped,
        "future_deleted": future_deleted,
        "positions_deleted": pos_result.rowcount,
        "retention_days": retention_days,
    }
