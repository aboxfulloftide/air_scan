import hashlib
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from api.db import get_db

router = APIRouter(prefix="/api/firmware", tags=["firmware"])

# Firmware binaries stored outside the api/ package, at project root level
FIRMWARE_DIR = Path(__file__).resolve().parent.parent.parent / "firmware_store"
FIRMWARE_DIR.mkdir(exist_ok=True)


@router.get("/check")
async def check_firmware(
    request: Request,
    scanner_name: str,
    version: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Called by ESP32 scanners after each successful observation upload.
    Returns update info if the stored current release differs from the
    scanner's running version.

    Query params:
        scanner_name  — scanner hostname (e.g. "esp32-static-1")
        version       — firmware version string the scanner is running (e.g. "1.1.0")
    """
    r = await db.execute(text("""
        SELECT version, filename, sha256, size_bytes, notes
        FROM firmware_releases
        WHERE platform = 'esp32' AND is_current = 1
        LIMIT 1
    """))
    row = r.mappings().first()

    if not row or row["version"] == version:
        return {"update_available": False}

    base = str(request.base_url).rstrip("/")
    return {
        "update_available": True,
        "version":    row["version"],
        "url":        f"{base}/api/firmware/download/{row['version']}",
        "sha256":     row["sha256"],
        "size_bytes": row["size_bytes"],
        "notes":      row["notes"] or "",
    }


@router.get("/releases")
async def list_releases(db: AsyncSession = Depends(get_db)):
    """List all firmware releases, newest first."""
    r = await db.execute(text("""
        SELECT id, version, platform, filename, sha256, size_bytes,
               notes, is_current, uploaded_at
        FROM firmware_releases
        ORDER BY uploaded_at DESC
    """))
    return [dict(row) for row in r.mappings().all()]


@router.post("/release")
async def upload_release(
    version:  str        = Form(...),
    platform: str        = Form("esp32"),
    notes:    str        = Form(""),
    make_current: bool   = Form(True),
    file:     UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """
    Upload a new firmware binary (.bin).
    Set make_current=true to immediately promote it as the active OTA target.
    """
    dest = FIRMWARE_DIR / file.filename
    sha = hashlib.sha256()
    size = 0

    with dest.open("wb") as f:
        while chunk := await file.read(65536):
            f.write(chunk)
            sha.update(chunk)
            size += len(chunk)

    digest = sha.hexdigest()

    await db.execute(text("""
        INSERT INTO firmware_releases
            (version, platform, filename, sha256, size_bytes, notes, is_current)
        VALUES
            (:ver, :plat, :fname, :sha256, :size, :notes, :cur)
        ON DUPLICATE KEY UPDATE
            filename   = VALUES(filename),
            sha256     = VALUES(sha256),
            size_bytes = VALUES(size_bytes),
            notes      = VALUES(notes),
            is_current = VALUES(is_current),
            uploaded_at = NOW()
    """), {
        "ver": version, "plat": platform, "fname": file.filename,
        "sha256": digest, "size": size, "notes": notes,
        "cur": int(make_current),
    })

    if make_current:
        # Clear current flag on all other releases for this platform
        await db.execute(text("""
            UPDATE firmware_releases
            SET is_current = 0
            WHERE platform = :plat AND version != :ver
        """), {"plat": platform, "ver": version})

    await db.commit()
    return {"version": version, "sha256": digest, "size_bytes": size, "is_current": make_current}


@router.patch("/release/{version}/promote")
async def promote_release(version: str, platform: str = "esp32", db: AsyncSession = Depends(get_db)):
    """Mark an existing release as the current OTA target."""
    r = await db.execute(text("""
        SELECT id FROM firmware_releases WHERE version = :ver AND platform = :plat
    """), {"ver": version, "plat": platform})
    if not r.first():
        raise HTTPException(status_code=404, detail="Release not found")

    await db.execute(text("""
        UPDATE firmware_releases SET is_current = 0 WHERE platform = :plat
    """), {"plat": platform})
    await db.execute(text("""
        UPDATE firmware_releases SET is_current = 1
        WHERE version = :ver AND platform = :plat
    """), {"ver": version, "plat": platform})
    await db.commit()
    return {"ok": True, "current": version}


@router.delete("/release/{version}")
async def delete_release(version: str, platform: str = "esp32", db: AsyncSession = Depends(get_db)):
    """Delete a firmware release record and its binary."""
    r = await db.execute(text("""
        SELECT filename, is_current FROM firmware_releases
        WHERE version = :ver AND platform = :plat
    """), {"ver": version, "plat": platform})
    row = r.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Release not found")
    if row["is_current"]:
        raise HTTPException(status_code=400, detail="Cannot delete the current release; promote another first")

    dest = FIRMWARE_DIR / row["filename"]
    if dest.exists():
        dest.unlink()

    await db.execute(text("""
        DELETE FROM firmware_releases WHERE version = :ver AND platform = :plat
    """), {"ver": version, "plat": platform})
    await db.commit()
    return {"ok": True}


@router.get("/download/{version}")
async def download_firmware(version: str, platform: str = "esp32", db: AsyncSession = Depends(get_db)):
    """
    Serve a firmware binary for OTA.
    The ESP32 HTTPUpdate library fetches this URL directly.
    """
    r = await db.execute(text("""
        SELECT filename FROM firmware_releases
        WHERE version = :ver AND platform = :plat
    """), {"ver": version, "plat": platform})
    row = r.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Firmware version not found")

    path = FIRMWARE_DIR / row["filename"]
    if not path.exists():
        raise HTTPException(status_code=404, detail="Binary file missing from server")

    return FileResponse(path, media_type="application/octet-stream", filename=row["filename"])
