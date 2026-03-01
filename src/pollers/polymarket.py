"""Polymarket market poller.

Uses the Gamma API (no auth required) to discover new events and markets.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import httpx

from src.models import MarketStatus, NewMarket, Platform
from src.pollers.base import BasePoller
from src.storage.sqlite import MarketStore

logger = logging.getLogger("oraclewatch")


class PolymarketPoller(BasePoller):
    platform = Platform.POLYMARKET

    def __init__(
        self,
        store: MarketStore,
        http_client: httpx.AsyncClient,
        gamma_api_url: str = "https://gamma-api.polymarket.com",
        lookback_seconds: int = 300,
    ) -> None:
        super().__init__(store, http_client)
        self._gamma_url = gamma_api_url.rstrip("/")
        self._lookback_seconds = lookback_seconds

    async def fetch_recent_markets(self) -> list[NewMarket]:
        """Fetch recent markets from the Polymarket Gamma API."""
        # Polymarket Gamma API supports ordering and pagination
        markets: list[NewMarket] = []

        # Fetch recent events (which contain markets)
        try:
            event_markets = await self._fetch_from_events()
            markets.extend(event_markets)
        except Exception as exc:
            logger.warning("[polymarket] Event fetch failed: %s", exc)

        # Also fetch markets directly for completeness
        try:
            direct_markets = await self._fetch_direct_markets()
            markets.extend(direct_markets)
        except Exception as exc:
            logger.warning("[polymarket] Direct market fetch failed: %s", exc)

        # Deduplicate by market_id
        seen_ids: set[str] = set()
        unique: list[NewMarket] = []
        for m in markets:
            if m.market_id not in seen_ids:
                seen_ids.add(m.market_id)
                unique.append(m)

        await self._store.set_last_poll_ts(Platform.POLYMARKET, int(time.time()))
        logger.debug("[polymarket] Fetched %d unique market(s)", len(unique))
        return unique

    async def _fetch_from_events(self) -> list[NewMarket]:
        """Fetch markets via the /events endpoint (gives event context)."""
        params = {
            "limit": 50,
            "active": "true",
            "order": "startDate",
            "ascending": "false",
        }
        resp = await self._http.get(f"{self._gamma_url}/events", params=params)
        resp.raise_for_status()
        events = resp.json()

        markets: list[NewMarket] = []
        for event in events:
            event_markets = event.get("markets", [])
            for m in event_markets:
                parsed = self._parse_market(m, event_title=event.get("title", ""))
                if parsed:
                    markets.append(parsed)
        return markets

    async def _fetch_direct_markets(self) -> list[NewMarket]:
        """Fetch markets directly from the /markets endpoint."""
        params = {
            "limit": 100,
            "active": "true",
            "order": "createdAt",
            "ascending": "false",
        }
        resp = await self._http.get(f"{self._gamma_url}/markets", params=params)
        resp.raise_for_status()
        raw_markets = resp.json()

        markets: list[NewMarket] = []
        for m in raw_markets:
            parsed = self._parse_market(m)
            if parsed:
                markets.append(parsed)
        return markets

    def _parse_market(self, raw: dict, event_title: str = ""):
        """Parse a Polymarket Gamma API market into our NewMarket model."""
        try:
            market_id = str(raw.get("id", raw.get("conditionId", "")))
            if not market_id:
                return None

            question = raw.get("question", raw.get("title", "Unknown Market"))
            title = f"{event_title}: {question}" if event_title and event_title != question else question

            # Parse outcomes and prices
            outcomes: list[str] = []
            outcome_prices: list[float] = []
            try:
                import json
                raw_outcomes = raw.get("outcomes", "[]")
                if isinstance(raw_outcomes, str):
                    outcomes = json.loads(raw_outcomes)
                elif isinstance(raw_outcomes, list):
                    outcomes = raw_outcomes

                raw_prices = raw.get("outcomePrices", "[]")
                if isinstance(raw_prices, str):
                    outcome_prices = [float(p) for p in json.loads(raw_prices)]
                elif isinstance(raw_prices, list):
                    outcome_prices = [float(p) for p in raw_prices]
            except (json.JSONDecodeError, ValueError, TypeError):
                pass

            yes_price = outcome_prices[0] if len(outcome_prices) > 0 else None
            no_price = outcome_prices[1] if len(outcome_prices) > 1 else None

            # Parse timestamps
            created_at = None
            if raw.get("createdAt"):
                try:
                    created_at = datetime.fromisoformat(
                        str(raw["createdAt"]).replace("Z", "+00:00")
                    )
                except (ValueError, TypeError):
                    pass

            close_time = None
            if raw.get("endDate"):
                try:
                    close_time = datetime.fromisoformat(
                        str(raw["endDate"]).replace("Z", "+00:00")
                    )
                except (ValueError, TypeError):
                    pass

            # Volume
            volume = None
            if raw.get("volume") is not None:
                try:
                    volume = float(raw["volume"])
                except (ValueError, TypeError):
                    pass

            # Market URL
            slug = raw.get("slug", "")
            url = f"https://polymarket.com/event/{slug}" if slug else ""

            # Category / tags
            category = ""
            tags = raw.get("tags", [])
            if tags and isinstance(tags, list):
                category = tags[0] if isinstance(tags[0], str) else str(tags[0])

            active = raw.get("active", True)
            status = MarketStatus.OPEN if active else MarketStatus.CLOSED

            return NewMarket(
                platform=Platform.POLYMARKET,
                market_id=market_id,
                title=title,
                url=url,
                status=status,
                created_at=created_at,
                close_time=close_time,
                category=category,
                yes_price=yes_price,
                no_price=no_price,
                volume=volume,
                outcomes=outcomes,
                outcome_prices=outcome_prices,
            )
        except Exception as exc:
            logger.warning("[polymarket] Failed to parse market: %s", exc)
            return None
