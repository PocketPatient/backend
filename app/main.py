from contextlib import asynccontextmanager
from datetime import datetime, timezone
import logging

import firebase_admin
import firebase_admin.credentials
import redis.asyncio as aioredis
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import settings
from app.middleware.logging import LoggingMiddleware
from app.middleware.rate_limit import RateLimitMiddleware
from app.routers import auth, courses, disease_documents, enrollments, sessions, units, users

logging.basicConfig(level=logging.INFO, format="%(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not firebase_admin._apps:
        if settings.firebase_credentials_path:
            cred = firebase_admin.credentials.Certificate(settings.firebase_credentials_path)
            firebase_admin.initialize_app(cred)
        elif settings.firebase_project_id:
            firebase_admin.initialize_app(options={"projectId": settings.firebase_project_id})
    app.state.redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    yield
    await app.state.redis.aclose()


app = FastAPI(title="PocketPatient API", version="0.1.0", lifespan=lifespan)
app.add_middleware(LoggingMiddleware)
app.add_middleware(RateLimitMiddleware)

_STATUS_TO_CODE: dict[int, str] = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    410: "GONE",
    422: "VALIDATION_ERROR",
    429: "RATE_LIMIT_EXCEEDED",
    500: "INTERNAL_ERROR",
}


@app.exception_handler(StarletteHTTPException)
async def custom_http_exception_handler(request: Request, exc: StarletteHTTPException):
    code = _STATUS_TO_CODE.get(exc.status_code, "ERROR")
    detail = exc.detail
    if isinstance(detail, dict):
        body = {"code": code, **detail}
    else:
        body = {"detail": detail, "code": code}
    return JSONResponse(status_code=exc.status_code, content=body)


@app.exception_handler(RequestValidationError)
async def custom_validation_exception_handler(request: Request, exc: RequestValidationError):
    # Pydantic v2 may include non-JSON-serializable objects (e.g. ValueError) in
    # ctx['error']; convert them to strings before serialising.
    errors = []
    for err in exc.errors():
        err = dict(err)
        if "ctx" in err and "error" in err["ctx"] and not isinstance(err["ctx"]["error"], str):
            err["ctx"] = {**err["ctx"], "error": str(err["ctx"]["error"])}
        errors.append(err)
    return JSONResponse(
        status_code=422,
        content={"detail": errors, "code": "VALIDATION_ERROR"},
    )


app.include_router(auth.router, prefix="/api/v1")
app.include_router(users.router, prefix="/api/v1")
app.include_router(courses.router, prefix="/api/v1")
app.include_router(disease_documents.router, prefix="/api/v1")
app.include_router(enrollments.router, prefix="/api/v1")
app.include_router(units.router, prefix="/api/v1")
app.include_router(sessions.router, prefix="/api/v1")


@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}
