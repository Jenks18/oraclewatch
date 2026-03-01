"""Polymarket event poller.

Uses the Gamma API (no auth required) to discover new prediction market events.
Fetches event-level data (not individual sub-market contracts).
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import List, Optional

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
        """Fetch recent events from the Polymarket Gamma API."""
        events = []  # type: List[NewMarket]

        try:
            resp = await self._http.get(
                "{}/events".format(self._gamma_url),
                params={
                    "limit": 100,
                    "active": "true",
                    "order": "createdAt",
                    "ascending": "false",
                },
            )
            resp.raise_for_status()
            raw_events = resp.json()

            for ev in raw_events:
                parsed = self._parse_event(ev)
                if parsed:
                    events.append(parsed)
        except Exception as exc:
            logger.warning("[polymarket] Event fetch failed: %s", exc)

        await self._store.set_last_poll_ts(Platform.POLYMARKET, int(time.time()))
        logger.debug("[polymarket] Fetched %d event(s)", len(events))
        return events

    def _parse_event(self, raw: dict) -> Optional[NewMarket]:
        """Parse a Polymarket event into a single NewMarket entry."""
        try:
            event_id = str(raw.get("id", ""))
            if not event_id:
                return None

            title = raw.get("title", "Unknown Event")
            slug = raw.get("slug", "")
            url = "https://polymarket.com/event/{}".format(slug) if slug else ""

            # Aggregate data from sub-markets
            sub_markets = raw.get("markets", [])
            market_count = len(sub_markets)
            subtitle = ""
            if market_count > 1:
                subtitle = "{} contract{}".format(market_count, "s" if market_count != 1 else "")

            # Get representative price from first active sub-market
            yes_price = None
            no_price = None
            total_volume = 0.0
            outcomes = []  # type: List[str]
            outcome_prices = []  # type: List[float]

            for sm in sub_markets:
                # Accumulate volume
                vol = sm.get("volume")
                if vol is not None:
                    try:
                        total_volume += float(vol)
                    except (ValueError, TypeError):
                        pass

            # Use first sub-market for representative pricing
            if sub_markets:
                first = sub_markets[0]
                try:
                    raw_outcomes = first.get("outcomes", "[]")
                    if isinstance(raw_outcomes, str):
                        outcomes = json.loads(raw_outcomes)
                    elif isinstance(raw_outcomes, list):
                        outcomes = raw_outcomes

                    raw_prices = first.get("outcomePrices", "[]")
                    if isinstance(raw_prices, str):
                        outcome_prices = [float(p) for p in json.loads(raw_prices)]
                    elif isinstance(raw_prices, list):
                        outcome_prices = [float(p) for p in raw_prices]
                except (json.JSONDecodeError, ValueError, TypeError):
                    pass

                yes_price = outcome_prices[0] if len(outcome_prices) > 0 else None
                no_price = outcome_prices[1] if len(outcome_prices) > 1 else None

            volume = total_volume if total_volume > 0 else None

            # Timestamps
            created_at = None
            created_str = raw.get("startDate", raw.get("createdAt"))
            if created_str:
                try:
                    created_at = datetime.fromisoformat(
                        str(created_str).replace("Z", "+00:00")
                    )
                except (ValueError, TypeError):
                    pass

            close_time = None
            end_str = raw.get("endDate")
            if end_str:
                try:
                    close_time = datetime.fromisoformat(
                        str(end_str).replace("Z", "+00:00")
                    )
                except (ValueError, TypeError):
                    pass

            # Category from tags — tags can be strings or dicts with 'label'
            category = ""
            tags = raw.get("tags", [])
            if tags and isinstance(tags, list):
                first_tag = tags[0]
                if isinstance(first_tag, str):
                    category = first_tag
                elif isinstance(first_tag, dict):
                    category = first_tag.get("label", "")
                else:
                    category = ""

            active = raw.get("active", True)
            status = MarketStatus.OPEN if active else MarketStatus.CLOSED

            return NewMarket(
                platform=Platform.POLYMARKET,
                market_id=event_id,
                title=title,
                subtitle=subtitle,
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
            logger.warning("[polymarket] Failed to parse event: %s", exc)
            return None
