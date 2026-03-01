"""SQLite storage layer – tracks seen markets, subscribers, and full market data."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import aiosqlite
from datetime import datetime

from src.models import NewMarket, Platform

_INIT_SQL = """
CREATE TABLE IF NOT EXISTS seen_markets (
    platform      TEXT    NOT NULL,
    market_id     TEXT    NOT NULL,
    title         TEXT    NOT NULL,
    detected_at   TEXT    NOT NULL,
    PRIMARY KEY (platform, market_id)
);

CREATE TABLE IF NOT EXISTS poll_state (
    platform            TEXT PRIMARY KEY,
    last_poll_timestamp  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS markets (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    platform        TEXT    NOT NULL,
    market_id       TEXT    NOT NULL,
    title           TEXT    NOT NULL,
    subtitle        TEXT    DEFAULT '',
    url             TEXT    DEFAULT '',
    status          TEXT    DEFAULT 'open',
    category        TEXT    DEFAULT '',
    yes_price       REAL,
    no_price        REAL,
    volume          REAL,
    event_ticker    TEXT    DEFAULT '',
    outcomes        TEXT    DEFAULT '[]',
    outcome_prices  TEXT    DEFAULT '[]',
    created_at      TEXT,
    close_time      TEXT,
    detected_at     TEXT    NOT NULL,
    UNIQUE(platform, market_id)
);

CREATE TABLE IF NOT EXISTS subscribers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id         TEXT    NOT NULL UNIQUE,
    username        TEXT    DEFAULT '',
    first_name      TEXT    DEFAULT '',
    subscribed_at   TEXT    NOT NULL,
    is_active       INTEGER DEFAULT 1,
    filter_keywords TEXT    DEFAULT ''
);
"""


class MarketStore:
    """Async SQLite-backed store for tracking which markets we've already seen."""

    def __init__(self, db_path: str = "oraclewatch.db") -> None:
        self._db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        self._db = await aiosqlite.connect(self._db_path)
        await self._db.executescript(_INIT_SQL)
        await self._db.commit()

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    async def is_seen(self, platform: Platform, market_id: str) -> bool:
        """Check if a market has already been recorded."""
        assert self._db
        cursor = await self._db.execute(
            "SELECT 1 FROM seen_markets WHERE platform = ? AND market_id = ?",
            (platform.value, market_id),
        )
        return await cursor.fetchone() is not None

    async def mark_seen(self, market: NewMarket) -> None:
        """Record a market so we don't alert on it again."""
        assert self._db
        await self._db.execute(
            "INSERT OR IGNORE INTO seen_markets (platform, market_id, title, detected_at) VALUES (?, ?, ?, ?)",
            (market.platform.value, market.market_id, market.title, market.detected_at.isoformat()),
        )
        await self._db.commit()

    async def mark_seen_batch(self, markets: list[NewMarket]) -> None:
        """Record multiple markets at once."""
        assert self._db
        await self._db.executemany(
            "INSERT OR IGNORE INTO seen_markets (platform, market_id, title, detected_at) VALUES (?, ?, ?, ?)",
            [
                (m.platform.value, m.market_id, m.title, m.detected_at.isoformat())
                for m in markets
            ],
        )
        await self._db.commit()

    async def get_last_poll_ts(self, platform: Platform) -> Optional[int]:
        """Get the last poll timestamp for a platform."""
        assert self._db
        cursor = await self._db.execute(
            "SELECT last_poll_timestamp FROM poll_state WHERE platform = ?",
            (platform.value,),
        )
        row = await cursor.fetchone()
        return row[0] if row else None

    async def set_last_poll_ts(self, platform: Platform, ts: int) -> None:
        """Update the last poll timestamp for a platform."""
        assert self._db
        await self._db.execute(
            "INSERT OR REPLACE INTO poll_state (platform, last_poll_timestamp) VALUES (?, ?)",
            (platform.value, ts),
        )
        await self._db.commit()

    async def seen_count(self, platform: Optional[Platform] = None) -> int:
        """Total number of seen markets, optionally filtered by platform."""
        assert self._db
        if platform:
            cursor = await self._db.execute(
                "SELECT COUNT(*) FROM seen_markets WHERE platform = ?",
                (platform.value,),
            )
        else:
            cursor = await self._db.execute("SELECT COUNT(*) FROM seen_markets")
        row = await cursor.fetchone()
        return row[0] if row else 0

    # ── Full market storage (for dashboard) ──────────────────────────

    async def store_market(self, market: NewMarket) -> None:
        """Store full market data for the web dashboard."""
        assert self._db
        await self._db.execute(
            """INSERT OR REPLACE INTO markets
               (platform, market_id, title, subtitle, url, status, category,
                yes_price, no_price, volume, event_ticker, outcomes, outcome_prices,
                created_at, close_time, detected_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                market.platform.value,
                market.market_id,
                market.title,
                market.subtitle,
                market.platform_url,
                market.status.value,
                market.category,
                market.yes_price,
                market.no_price,
                market.volume,
                market.event_ticker,
                json.dumps(market.outcomes),
                json.dumps(market.outcome_prices),
                market.created_at.isoformat() if market.created_at else None,
                market.close_time.isoformat() if market.close_time else None,
                market.detected_at.isoformat(),
            ),
        )
        await self._db.commit()

    async def store_markets_batch(self, markets: List[NewMarket]) -> None:
        """Store multiple markets at once."""
        assert self._db
        await self._db.executemany(
            """INSERT OR REPLACE INTO markets
               (platform, market_id, title, subtitle, url, status, category,
                yes_price, no_price, volume, event_ticker, outcomes, outcome_prices,
                created_at, close_time, detected_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    m.platform.value, m.market_id, m.title, m.subtitle,
                    m.platform_url, m.status.value, m.category,
                    m.yes_price, m.no_price, m.volume, m.event_ticker,
                    json.dumps(m.outcomes), json.dumps(m.outcome_prices),
                    m.created_at.isoformat() if m.created_at else None,
                    m.close_time.isoformat() if m.close_time else None,
                    m.detected_at.isoformat(),
                )
                for m in markets
            ],
        )
        await self._db.commit()

    async def get_recent_markets(
        self,
        platform: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Get recent markets for the dashboard."""
        assert self._db
        if platform:
            cursor = await self._db.execute(
                "SELECT * FROM markets WHERE platform = ? ORDER BY detected_at DESC LIMIT ? OFFSET ?",
                (platform, limit, offset),
            )
        else:
            cursor = await self._db.execute(
                "SELECT * FROM markets ORDER BY detected_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            )
        columns = [desc[0] for desc in cursor.description]
        rows = await cursor.fetchall()
        return [dict(zip(columns, row)) for row in rows]

    async def get_market_count(self, platform: Optional[str] = None) -> int:
        """Get total market count."""
        assert self._db
        if platform:
            cursor = await self._db.execute(
                "SELECT COUNT(*) FROM markets WHERE platform = ?", (platform,)
            )
        else:
            cursor = await self._db.execute("SELECT COUNT(*) FROM markets")
        row = await cursor.fetchone()
        return row[0] if row else 0

    # ── Subscriber management ────────────────────────────────────────

    async def add_subscriber(
        self, chat_id: str, username: str = "", first_name: str = ""
    ) -> bool:
        """Add a Telegram subscriber. Returns True if newly added."""
        assert self._db
        try:
            await self._db.execute(
                """INSERT INTO subscribers (chat_id, username, first_name, subscribed_at, is_active)
                   VALUES (?, ?, ?, ?, 1)
                   ON CONFLICT(chat_id) DO UPDATE SET is_active = 1, username = ?, first_name = ?""",
                (chat_id, username, first_name, datetime.utcnow().isoformat(), username, first_name),
            )
            await self._db.commit()
            return True
        except Exception:
            return False

    async def remove_subscriber(self, chat_id: str) -> bool:
        """Deactivate a subscriber."""
        assert self._db
        await self._db.execute(
            "UPDATE subscribers SET is_active = 0 WHERE chat_id = ?", (chat_id,)
        )
        await self._db.commit()
        return True

    async def get_active_subscribers(self) -> List[Dict[str, Any]]:
        """Get all active Telegram subscribers."""
        assert self._db
        cursor = await self._db.execute(
            "SELECT chat_id, username, first_name, filter_keywords FROM subscribers WHERE is_active = 1"
        )
        columns = [desc[0] for desc in cursor.description]
        rows = await cursor.fetchall()
        return [dict(zip(columns, row)) for row in rows]

    async def get_subscriber_count(self) -> int:
        """Count active subscribers."""
        assert self._db
        cursor = await self._db.execute(
            "SELECT COUNT(*) FROM subscribers WHERE is_active = 1"
        )
        row = await cursor.fetchone()
        return row[0] if row else 0

    async def set_subscriber_filter(self, chat_id: str, keywords: str) -> None:
        """Set keyword filter for a subscriber."""
        assert self._db
        await self._db.execute(
            "UPDATE subscribers SET filter_keywords = ? WHERE chat_id = ?",
            (keywords, chat_id),
        )
        await self._db.commit()

    async def clear_markets(self) -> None:
        """Delete all market data and seen records (for clean re-seed)."""
        assert self._db
        await self._db.execute("DELETE FROM markets")
        await self._db.execute("DELETE FROM seen_markets")
        await self._db.execute("DELETE FROM poll_state")
        await self._db.commit()
