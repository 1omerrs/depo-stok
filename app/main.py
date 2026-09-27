from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.config import Settings
from app.matching import Matcher
from app.seed import seed_if_empty
from app.service import WarehouseService
from app.web import router

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"


def build_store(settings: Settings):
    if settings.data_backend == "airtable":
        if not settings.airtable_api_key or not settings.airtable_base_id:
            raise RuntimeError("AIRTABLE_API_KEY ve AIRTABLE_BASE_ID gerekli")
        from app.airtable_store import AirtableStore

        return AirtableStore(settings)
    from app.sqlite_store import SqliteStore

    return SqliteStore(settings.sqlite_path)


def create_app() -> FastAPI:
    logging.basicConfig(level=logging.INFO)
    settings = Settings.from_env()
    store = build_store(settings)
    if settings.data_backend == "sqlite":
        seed_if_empty(store)
    service = WarehouseService(store, settings, Matcher(settings))
    app = FastAPI(title="Akıllı Depo Stok Takip")
    app.add_middleware(SessionMiddleware, secret_key=settings.panel_secret, session_cookie="depo_session", same_site="lax", max_age=60 * 60 * 12)
    app.state.settings = settings
    app.state.store = store
    app.state.service = service
    app.include_router(router)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    @app.get("/giris")
    @app.get("/uye-ol")
    @app.get("/yonetim")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.exception_handler(HTTPException)
    def http_error(_request: Request, exc: HTTPException):
        detail = exc.detail if isinstance(exc.detail, str) else "İstek reddedildi"
        return JSONResponse({"ok": False, "code": "error", "message": detail, "data": {}}, status_code=exc.status_code)

    @app.exception_handler(Exception)
    def unexpected(_request: Request, exc: Exception):
        log.exception("beklenmeyen hata")
        return JSONResponse({"ok": False, "code": "error", "message": "Beklenmeyen bir hata oluştu", "data": {}}, status_code=500)

    return app


def __getattr__(name: str):
    if name == "app":
        global _app
        try:
            return _app
        except NameError:
            _app = create_app()
            return _app
    raise AttributeError(name)
