"""Abstract base poller that platform-specific pollers implement."""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod

import httpx

from src.models import NewMarket, Platform
from src.storage.sqlite import MarketStore

logger = logging.getLogger("oraclewatch")


class BasePoller(ABC):
    """Base class for market pollers."""

    platform: Platform

    def __init__(self, store: MarketStore, http_client: httpx.AsyncClient) -> None:
        self._store = store
        self._http = http_client

    @abstractmethod
    async def fetch_recent_markets(self) -> list[NewMarket]:
        """Fetch the latest markets from the platform API.

        Should return ALL currently-relevant markets; filtering for 'new'
        ones (not yet seen) is handled by the orchestrator.
        """
        ...

    async def poll(self) -> list[NewMarket]:
        """Run one poll cycle: fetch markets, filter unseen, mark as seen.

        Returns only the *new* markets discovered in this cycle.
        """
        try:
            all_markets = await self.fetch_recent_markets()
        except httpx.HTTPStatusError as exc:
            logger.error(
                "[%s] HTTP %s from API: %s",
                self.platform.value,
                exc.response.status_code,
                exc.response.text[:300],
            )
            return []
        except (httpx.RequestError, asyncio.TimeoutError) as exc:
            logger.error("[%s] Request failed: %s", self.platform.value, exc)
            return []

        new_markets: list[NewMarket] = []
        for market in all_markets:
            if not await self._store.is_seen(self.platform, market.market_id):
                new_markets.append(market)

        if new_markets:
            await self._store.mark_seen_batch(new_markets)
            logger.info(
                "[%s] Discovered %d new market(s)", self.platform.value, len(new_markets)
            )

        return new_markets
