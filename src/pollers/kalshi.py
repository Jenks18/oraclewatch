"""Kalshi market poller.

Uses GET /markets with min_created_ts to efficiently discover newly deployed markets.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Optional

import httpx

from src.models import MarketStatus, NewMarket, Platform
from src.pollers.base import BasePoller
from src.storage.sqlite import MarketStore

logger = logging.getLogger("oraclewatch")

# Map Kalshi status strings to our enum
_STATUS_MAP = {
    "unopened": MarketStatus.OPEN,
    "open": MarketStatus.OPEN,
    "paused": MarketStatus.OPEN,
    "closed": MarketStatus.CLOSED,
    "settled": MarketStatus.SETTLED,
}


class KalshiPoller(BasePoller):
    platform = Platform.KALSHI

    def __init__(
        self,
        store: MarketStore,
        http_client: httpx.AsyncClient,
        base_url: str = "https://api.elections.kalshi.com/trade-api/v2",
        lookback_seconds: int = 300,
    ) -> None:
        super().__init__(store, http_client)
        self._base_url = base_url.rstrip("/")
        self._lookback_seconds = lookback_seconds

    async def fetch_recent_markets(self) -> list[NewMarket]:
        """Fetch recently created markets from Kalshi using timestamp pagination."""
        # Use stored poll timestamp, or fall back to lookback window
        last_ts = await self._store.get_last_poll_ts(Platform.KALSHI)
        if last_ts is None:
            last_ts = int(time.time()) - self._lookback_seconds

        markets: list[NewMarket] = []
        cursor = None  # type: Optional[str]
        pages_fetched = 0
        max_pages = 10  # safety limit

        while pages_fetched < max_pages:
            params: dict = {
                "limit": 200,
                "min_created_ts": last_ts,
            }
            if cursor:
                params["cursor"] = cursor

            # Rate-limit: pause between paginated requests
            if pages_fetched > 0:
                await asyncio.sleep(0.5)

            resp = await self._http.get(f"{self._base_url}/markets", params=params)

            # Handle rate limiting with retry
            if resp.status_code == 429:
                retry_after = float(resp.headers.get("Retry-After", "2"))
                logger.warning("[kalshi] Rate limited, waiting %.1fs...", retry_after)
                await asyncio.sleep(retry_after)
                continue

            resp.raise_for_status()
            data = resp.json()

            raw_markets = data.get("markets", [])
            if not raw_markets:
                break

            for m in raw_markets:
                market = self._parse_market(m)
                if market:
                    markets.append(market)

            cursor = data.get("cursor")
            if not cursor:
                break
            pages_fetched += 1

        # Update poll timestamp to now
        await self._store.set_last_poll_ts(Platform.KALSHI, int(time.time()))

        logger.debug("[kalshi] Fetched %d market(s) since ts=%d", len(markets), last_ts)
        return markets

    def _parse_market(self, raw: dict):
        """Parse a single Kalshi market API response into a NewMarket model."""
        try:
            ticker = raw.get("ticker", "")
            event_ticker = raw.get("event_ticker", "")
            title = raw.get("title", "Unknown Market")
            subtitle = raw.get("subtitle", "")

            # Parse timestamps
            created_time = None
            if raw.get("created_time"):
                try:
                    created_time = datetime.fromisoformat(
                        raw["created_time"].replace("Z", "+00:00")
                    )
                except (ValueError, TypeError):
                    pass

            close_time = None
            if raw.get("close_time"):
                try:
                    close_time = datetime.fromisoformat(
                        raw["close_time"].replace("Z", "+00:00")
                    )
                except (ValueError, TypeError):
                    pass

            # Parse prices – Kalshi returns cents (0-100) or dollar strings
            yes_price = None
            no_price = None
            if raw.get("yes_bid_dollars"):
                try:
                    yes_price = float(raw["yes_bid_dollars"])
                except (ValueError, TypeError):
                    pass
            elif raw.get("yes_bid") is not None:
                yes_price = raw["yes_bid"] / 100.0

            if raw.get("no_bid_dollars"):
                try:
                    no_price = float(raw["no_bid_dollars"])
                except (ValueError, TypeError):
                    pass
            elif raw.get("no_bid") is not None:
                no_price = raw["no_bid"] / 100.0

            volume = None
            if raw.get("volume") is not None:
                volume = float(raw["volume"])

            status = _STATUS_MAP.get(raw.get("status", ""), MarketStatus.UNKNOWN)

            return NewMarket(
                platform=Platform.KALSHI,
                market_id=ticker,
                title=title,
                subtitle=subtitle,
                url=f"https://kalshi.com/markets/{event_ticker}",
                status=status,
                created_at=created_time,
                close_time=close_time,
                yes_price=yes_price,
                no_price=no_price,
                volume=volume,
                event_ticker=event_ticker,
            )
        except Exception as exc:
            logger.warning("[kalshi] Failed to parse market: %s — %s", raw.get("ticker"), exc)
            return None
