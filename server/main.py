"""Điểm vào FastAPI: create_app(), nhiệm vụ nền xóa dữ liệu quá hạn 90 ngày."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import services
from .categories import CategoryDB
from .config import Settings, load_settings
from .db import make_engine, make_session_factory
from .deps import AuthRedirect
from .models import Base
from .routers import agent_api, parent_api, web
from .security import RateLimiter, Vault
from .ws import ConnectionManager

HERE = Path(__file__).resolve().parent
CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; "
       "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    Vault.set_key(settings.master_key)
    engine = make_engine(settings.db_url)
    Base.metadata.create_all(engine)
    SessionLocal = make_session_factory(engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async def retention_loop():
            while True:
                try:
                    with SessionLocal() as db:
                        await asyncio.to_thread(services.purge_old, db, settings)
                except Exception:  # không để tác vụ nền làm sập server
                    pass
                await asyncio.sleep(6 * 3600)
        task = asyncio.create_task(retention_loop())
        yield
        task.cancel()

    app = FastAPI(title="Open Guardian Kids - Server", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings, app.state.SessionLocal, app.state.engine = settings, SessionLocal, engine
    app.state.categories = CategoryDB(settings.categories_csv)
    app.state.manager = ConnectionManager()
    app.state.rl_login = RateLimiter(20, 60)
    app.state.rl_enroll = RateLimiter(10, 60)

    @app.exception_handler(AuthRedirect)
    async def _auth(request: Request, exc: AuthRedirect):
        if request.url.path.startswith("/api"):
            return JSONResponse({"detail": "Cần đăng nhập."}, status_code=401)
        return RedirectResponse("/login", status_code=303)

    @app.middleware("http")
    async def _headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers.update({"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Content-Security-Policy": CSP,
                             "Referrer-Policy": "same-origin", "Cache-Control": "no-store" if not request.url.path.startswith("/static") else "public, max-age=300"})
        return resp

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")
    app.include_router(agent_api.router)
    app.include_router(parent_api.router)
    app.include_router(web.router)
    return app
