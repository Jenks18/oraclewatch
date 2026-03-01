"""FastAPI web application — dashboard + JSON API."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from src.storage.sqlite import MarketStore

_WEB_DIR = Path(__file__).parent
_TEMPLATES_DIR = _WEB_DIR / "templates"
_STATIC_DIR = _WEB_DIR / "static"

PAGE_SIZE = 50


def create_app(store: MarketStore, telegram_bot_username: str = "") -> FastAPI:
    """Create the FastAPI app with access to the shared store."""

    app = FastAPI(title="OracleWatch", docs_url="/docs")
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

    # ── Dashboard ─────────────────────────────────────────────────

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request, platform: Optional[str] = None):
        markets = await store.get_recent_markets(platform=platform, limit=PAGE_SIZE)
        kalshi_count = await store.get_market_count("kalshi")
        poly_count = await store.get_market_count("polymarket")
        subscriber_count = await store.get_subscriber_count()
        total = await store.get_market_count(platform)
        has_more = total > PAGE_SIZE

        # Parse JSON fields for template
        for m in markets:
            try:
                m["outcomes"] = json.loads(m.get("outcomes", "[]"))
            except (json.JSONDecodeError, TypeError):
                m["outcomes"] = []
            try:
                m["outcome_prices"] = json.loads(m.get("outcome_prices", "[]"))
            except (json.JSONDecodeError, TypeError):
                m["outcome_prices"] = []

        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "markets": markets,
                "kalshi_count": kalshi_count,
                "poly_count": poly_count,
                "subscriber_count": subscriber_count,
                "has_more": has_more,
                "telegram_bot_username": telegram_bot_username,
            },
        )

    # ── JSON API ──────────────────────────────────────────────────

    @app.get("/api/markets")
    async def api_markets(
        platform: Optional[str] = None,
        page: int = Query(1, ge=1),
        limit: int = Query(PAGE_SIZE, ge=1, le=200),
    ):
        offset = (page - 1) * limit
        markets = await store.get_recent_markets(platform=platform, limit=limit, offset=offset)
        total = await store.get_market_count(platform)
        has_more = offset + limit < total

        for m in markets:
            try:
                m["outcomes"] = json.loads(m.get("outcomes", "[]"))
            except (json.JSONDecodeError, TypeError):
                m["outcomes"] = []
            try:
                m["outcome_prices"] = json.loads(m.get("outcome_prices", "[]"))
            except (json.JSONDecodeError, TypeError):
                m["outcome_prices"] = []

        return JSONResponse({
            "markets": markets,
            "total": total,
            "page": page,
            "has_more": has_more,
        })

    @app.get("/api/stats")
    async def api_stats():
        return JSONResponse({
            "kalshi_count": await store.get_market_count("kalshi"),
            "poly_count": await store.get_market_count("polymarket"),
            "total_count": await store.get_market_count(),
            "subscriber_count": await store.get_subscriber_count(),
        })

    @app.get("/api/health")
    async def health():
        return {"status": "ok"}

    return app
