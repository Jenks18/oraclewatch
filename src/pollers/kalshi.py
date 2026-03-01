"""Kalshi event poller.

Uses GET /events to fetch top-level prediction markets (events), NOT
individual sub-market contracts. Each Kalshi "event" (e.g. "Will Bitcoin
hit $100k?") may contain many granular contracts; we only track the event.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

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
        base_url: str = "https://trading-api.kalshi.com/trade-api/v2",
        lookback_seconds: int = 300,
    ) -> None:
        super().__init__(store, http_client)
        self._base_url = base_url.rstrip("/")
        self._lookback_seconds = lookback_seconds

    async def fetch_recent_markets(self) -> list[NewMarket]:
        """Fetch recent events from Kalshi (event-level, not sub-contracts)."""
        events = []  # type: List[NewMarket]
        cursor = None  # type: Optional[str]
        pages_fetched = 0
        max_pages = 10

        while pages_fetched < max_pages:
            params = {
                "limit": 200,
                "status": "open",
            }  # type: Dict[str, object]
            if cursor:
                params["cursor"] = cursor

            if pages_fetched > 0:
                await asyncio.sleep(0.5)

            resp = await self._http.get("{}/events".format(self._base_url), params=params)

            if resp.status_code == 429:
                retry_after = float(resp.headers.get("Retry-After", "2"))
                logger.warning("[kalshi] Rate limited, waiting %.1fs...", retry_after)
                await asyncio.sleep(retry_after)
                continue

            resp.raise_for_status()
            data = resp.json()

            raw_events = data.get("events", [])
            if not raw_events:
                break

            for ev in raw_events:
                parsed = self._parse_event(ev)
                if parsed:
                    events.append(parsed)

            cursor = data.get("cursor")
            if not cursor:
                break
            pages_fetched += 1

        await self._store.set_last_poll_ts(Platform.KALSHI, int(time.time()))
        logger.debug("[kalshi] Fetched %d event(s)", len(events))
        return events

    def _parse_event(self, raw: dict) -> Optional[NewMarket]:
        """Parse a Kalshi event into a single NewMarket entry."""
        try:
            event_ticker = raw.get("event_ticker", "")
            if not event_ticker:
                return None

            title = raw.get("title", "Unknown Event")
            subtitle = raw.get("sub_title", raw.get("subtitle", ""))
            category = raw.get("category", "")

            # Use the event's mutually_exclusive field or count of markets
            sub_markets = raw.get("markets", [])
            market_count = len(sub_markets) if sub_markets else raw.get("market_count", 0)
            if market_count and not subtitle:
                subtitle = "{} contract{}".format(market_count, "s" if market_count != 1 else "")

            # Get representative price from first active sub-market
            yes_price = None
            no_price = None
            volume = None
            status = MarketStatus.OPEN

            if sub_markets:
                # Pick the first open sub-market for a representative price
                for sm in sub_markets:
                    sm_status = _STATUS_MAP.get(sm.get("status", ""), MarketStatus.UNKNOWN)
                    if sm_status == MarketStatus.OPEN:
                        if sm.get("yes_bid") is not None:
                            yes_price = sm["yes_bid"] / 100.0
                        if sm.get("no_bid") is not None:
                            no_price = sm["no_bid"] / 100.0
                        break

                # Sum volume across all sub-markets
                total_vol = 0
                for sm in sub_markets:
                    v = sm.get("volume")
                    if v is not None:
                        total_vol += float(v)
                if total_vol > 0:
                    volume = total_vol

            # Parse open/close times from the event or first sub-market
            created_at = None
            close_time = None

            created_str = raw.get("created_time")
            if not created_str and sub_markets:
                created_str = sub_markets[0].get("created_time")
            if created_str:
                try:
                    created_at = datetime.fromisoformat(
                        str(created_str).replace("Z", "+00:00")
                    )
                except (ValueError, TypeError):
                    pass

            close_str = raw.get("close_time", raw.get("expected_expiration_time"))
            if not close_str and sub_markets:
                close_str = sub_markets[0].get("close_time")
            if close_str:
                try:
                    close_time = datetime.fromisoformat(
                        str(close_str).replace("Z", "+00:00")
                    )
                except (ValueError, TypeError):
                    pass

            return NewMarket(
                platform=Platform.KALSHI,
                market_id=event_ticker,
                title=title,
                subtitle=subtitle,
                url="https://kalshi.com/markets/{}".format(event_ticker),
                status=status,
                created_at=created_at,
                close_time=close_time,
                category=category,
                yes_price=yes_price,
                no_price=no_price,
                volume=volume,
                event_ticker=event_ticker,
            )
        except Exception as exc:
            logger.warning("[kalshi] Failed to parse event: %s — %s", raw.get("event_ticker"), exc)
            return None
