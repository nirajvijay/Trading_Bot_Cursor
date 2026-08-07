"""NIFTY RADAR read-only observation API.

Local development:
    uvicorn api.main:app --host 127.0.0.1 --port 8000

Website auth: /api/v1/account/*
Kite market-data token: /api/v1/auth/*
Private APIs require a website session when WEB_AUTH_ENABLED=true.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.auth.settings import validate_startup_settings
from api.routers.account import router as account_router
from api.routers.auth import router as auth_router
from api.routers.checklist import router as checklist_router
from api.routers.observation import router as observation_router
from api.routers.sessions import router


@asynccontextmanager
async def lifespan(_app: FastAPI):
    validate_startup_settings()
    yield


app = FastAPI(title="NIFTY RADAR API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api/v1")
app.include_router(auth_router, prefix="/api/v1")
app.include_router(account_router, prefix="/api/v1")
app.include_router(checklist_router, prefix="/api/v1")
app.include_router(observation_router, prefix="/api/v1")
