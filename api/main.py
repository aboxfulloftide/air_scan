from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path

from api.dashboard.router import router as dashboard_router
from api.devices.router import router as devices_router
from api.scanners.router import router as scanners_router
from api.maps.router import router as maps_router
from api.deploy.router import router as deploy_router
from api.maintenance.router import router as maintenance_router
from api.observations.router import router as observations_router

app = FastAPI(title="Air Scan", version="0.1.0")

app.include_router(dashboard_router)
app.include_router(devices_router)
app.include_router(scanners_router)
app.include_router(maps_router)
app.include_router(deploy_router)
app.include_router(maintenance_router)
app.include_router(observations_router)

# Serve built frontend from static/
static_dir = Path(__file__).resolve().parent.parent / "static"
if static_dir.exists():
    app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")

    @app.get("/{path:path}")
    async def serve_spa(path: str):
        file = static_dir / path
        if file.exists() and file.is_file():
            return FileResponse(file)
        return FileResponse(static_dir / "index.html")
