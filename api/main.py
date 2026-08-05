"""NIFTY RADAR read-only observation API.

Local development:
    uvicorn api.main:app --host 127.0.0.1 --port 8000

Auth endpoints under /api/v1/auth are for localhost use only. Do not bind to 0.0.0.0.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routers.auth import router as auth_router
from api.routers.checklist import router as checklist_router
from api.routers.observation import router as observation_router
from api.routers.sessions import router

app = FastAPI(title="NIFTY RADAR API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api/v1")
app.include_router(auth_router, prefix="/api/v1")
app.include_router(checklist_router, prefix="/api/v1")
app.include_router(observation_router, prefix="/api/v1")
