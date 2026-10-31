from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend import db
from backend.api import core_router, procurement_router, review_router, settings_router, shipment_router, tenant_router, workflow_router
from backend.api.admin_report import router as admin_report_router

app = FastAPI(
    title="MedGuard AI",
    description="Local, fictional multi-agent hospital medicine supply resilience prototype.",
    version="1.0.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT"],
    allow_headers=["*"],
)
db.initialize()
for router in (core_router, workflow_router, review_router, procurement_router, shipment_router, settings_router):
    app.include_router(router)
app.include_router(tenant_router)
app.include_router(admin_report_router)

DIST_DIR = Path(__file__).resolve().parents[1] / "dist"
ASSET_DIR = DIST_DIR / "assets"
if ASSET_DIR.exists():
    app.mount("/assets", StaticFiles(directory=ASSET_DIR), name="assets")


@app.get("/")
def root():
    index = DIST_DIR / "index.html"
    if index.exists():
        return FileResponse(index)
    return {"service": "MedGuard AI", "frontend": "Run npm run dev in the project root or npm run build first."}
