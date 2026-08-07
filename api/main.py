"""NIFTY RADAR read-only observation API.

Local development:
    uvicorn api.main:app --host 127.0.0.1 --port 8000

Production (behind nginx on the same host):
    uvicorn api.main:app \\
        --host 127.0.0.1 --port 8000 --workers 1 \\
        --proxy-headers --forwarded-allow-ips=127.0.0.1

Website auth: /api/v1/account/*
Kite market-data token: /api/v1/auth/*
Private APIs require a website session when WEB_AUTH_ENABLED=true.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from api.auth.settings import cors_origins, validate_startup_settings
from api.routers.account import router as account_router
from api.routers.auth import router as auth_router
from api.routers.checklist import router as checklist_router
from api.routers.observation import router as observation_router
from api.routers.sessions import router

ALLOWED_HOSTS = [
    "njtrading.website",
    "www.njtrading.website",
    "127.0.0.1",
    "localhost",
]


@asynccontextmanager
async def lifespan(_app: FastAPI):
    validate_startup_settings()
    yield


app = FastAPI(title="NIFTY RADAR API", version="1.0.0", lifespan=lifespan)

# CORS allow_origins derived from the same WEB_AUTH_ORIGIN_ALLOWLIST as CSRF.
# Add CORS first, then TrustedHost so TrustedHost is outermost.
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)

app.include_router(router, prefix="/api/v1")
app.include_router(auth_router, prefix="/api/v1")
app.include_router(account_router, prefix="/api/v1")
app.include_router(checklist_router, prefix="/api/v1")
app.include_router(observation_router, prefix="/api/v1")
