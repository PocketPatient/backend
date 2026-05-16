from datetime import datetime, timezone

from fastapi import FastAPI

from app.routers import auth

app = FastAPI(title="PocketPatient API", version="0.1.0")

app.include_router(auth.router, prefix="/api/v1")


@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}
