"""Health Check Router"""
import time
import shutil
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from datetime import datetime
from database.connection import DatabaseManager
from config.settings import settings

router = APIRouter(tags=["Health"])

@router.get("/health")
async def health_simple():
    return {"status": "ok", "timestamp": time.time()}

@router.get("/health/full")
async def health_full():
    checks = {"status": "healthy", "timestamp": datetime.utcnow().isoformat(), "components": {}}
    try:
        checks["components"]["database"] = "healthy" if DatabaseManager.health_check() else "unhealthy"
        if checks["components"]["database"] == "unhealthy":
            checks["status"] = "degraded"
    except Exception as e:
        checks["components"]["database"] = f"error: {e}"
        checks["status"] = "degraded"
    try:
        disk = shutil.disk_usage(settings.image.TEMP_UPLOAD_DIR)
        free_gb = disk.free / (1024**3)
        checks["components"]["disk_space"] = {"free_gb": round(free_gb, 2), "status": "healthy" if free_gb > 1 else "warning"}
    except:
        pass
    return JSONResponse(content=checks, status_code=200 if checks["status"]=="healthy" else 503)
