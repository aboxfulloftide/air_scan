from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path

from api.middleware.conn_limit import PerIPConnectionLimitMiddleware

from api.dashboard.router import router as dashboard_router
from api.devices.router import router as devices_router
from api.scanners.router import router as scanners_router
from api.maps.router import router as maps_router
from api.deploy.router import router as deploy_router
from api.maintenance.router import router as maintenance_router
from api.observations.router import router as observations_router
from api.firmware.router import router as firmware_router
from api.mobile.router import router as mobile_router
from api.calibration.router import router as calibration_router

app = FastAPI(title="Air Scan", version="0.1.0")
app.add_middleware(PerIPConnectionLimitMiddleware, max_per_ip=10, request_timeout=10)

app.include_router(dashboard_router)
app.include_router(devices_router)
app.include_router(scanners_router)
app.include_router(maps_router)
app.include_router(deploy_router)
app.include_router(maintenance_router)
app.include_router(observations_router)
app.include_router(firmware_router)
app.include_router(mobile_router)
app.include_router(calibration_router)

# Serve built frontend from static/
static_dir = Path(__file__).resolve().parent.parent / "static"
if static_dir.exists():
    app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")

    @app.get("/{path:path}")
    async def serve_spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not found")
        file = static_dir / path
        if file.exists() and file.is_file():
            return FileResponse(file)
        return FileResponse(static_dir / "index.html")
