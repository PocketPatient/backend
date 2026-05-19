from contextlib import asynccontextmanager
from datetime import datetime, timezone

import firebase_admin
import redis.asyncio as aioredis
from fastapi import FastAPI

from app.config import settings
from app.routers import auth, users, courses


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not firebase_admin._apps and settings.firebase_project_id:
        firebase_admin.initialize_app(options={"projectId": settings.firebase_project_id})
    app.state.redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    yield
    await app.state.redis.aclose()


app = FastAPI(title="PocketPatient API", version="0.1.0", lifespan=lifespan)

app.include_router(auth.router, prefix="/api/v1")
app.include_router(users.router, prefix="/api/v1")
app.include_router(courses.router, prefix="/api/v1")


@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}
