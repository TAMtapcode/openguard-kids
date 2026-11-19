"""Điểm vào FastAPI: create_app(), nhiệm vụ nền xóa dữ liệu quá hạn 90 ngày."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import services
from .categories import CategoryDB
from .config import Settings, load_settings
from .db import make_engine, make_session_factory
from .deps import AuthRedirect
from .models import Base
from .routers import agent_api, parent_api, web
from .security import RateLimiter, Vault
from .ws import ConnectionManager, ParentHub

HERE = Path(__file__).resolve().parent
CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; "
       "connect-src 'self' ws: wss:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
NO_UI = ("<!doctype html><meta charset='utf-8'><title>Open Guardian Kids</title>"
         "<p>Giao diện web chưa được build. Chạy <code>cd web &amp;&amp; npm ci &amp;&amp; npm run build</code> rồi khởi động lại máy chủ.</p>")


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
    app.state.parent_hub = ParentHub()
    app.state.rl_login = RateLimiter(20, 60)
    app.state.rl_enroll = RateLimiter(10, 60)

    @app.exception_handler(AuthRedirect)
    async def _auth(request: Request, exc: AuthRedirect):
        return JSONResponse({"detail": "Cần đăng nhập."}, status_code=401)

    app.add_middleware(CORSMiddleware, allow_origin_regex=settings.cors_origin_regex, allow_credentials=False,
                       allow_private_network=True,   # Chrome: trang Netlify (Internet) gọi máy chủ trong mạng nhà
                       allow_methods=["GET", "POST", "PUT", "DELETE"], allow_headers=["Authorization", "Content-Type"], max_age=600)

    @app.middleware("http")
    async def _headers(request: Request, call_next):
        resp = await call_next(request)
        cache = "public, max-age=31536000, immutable" if request.url.path.startswith("/assets/") else "no-store"
        resp.headers.update({"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Content-Security-Policy": CSP,
                             "Referrer-Policy": "same-origin", "Cache-Control": cache})
        return resp

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    app.include_router(agent_api.router)
    app.include_router(parent_api.router)
    app.include_router(web.router)
    dist = Path(settings.web_dist)
    if (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=str(dist / "assets")), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        """Giao diện React (một trang): mọi đường dẫn không phải API trả về index.html để React Router xử lý."""
        if path.startswith(("api/", "assets/")):
            raise HTTPException(404, "Không tìm thấy.")
        f = dist / path
        if path and f.is_file() and f.resolve().is_relative_to(dist.resolve()):
            return FileResponse(f)
        index = dist / "index.html"
        return FileResponse(index) if index.exists() else HTMLResponse(NO_UI)
    return app
