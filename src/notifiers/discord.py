"""Discord webhook notifier – sends embed messages to a channel."""

from __future__ import annotations

import logging

import httpx

from src.models import NewMarket, Platform
from src.notifiers.base import BaseNotifier

logger = logging.getLogger("oraclewatch")

_PLATFORM_COLORS = {
    Platform.KALSHI: 0x00BFFF,      # cyan
    Platform.POLYMARKET: 0x9B59B6,   # purple
}


class DiscordNotifier(BaseNotifier):
    """Send new-market alerts as Discord webhook embeds."""

    def __init__(self, webhook_url: str, http_client: httpx.AsyncClient | None = None) -> None:
        self._webhook_url = webhook_url
        self._http = http_client or httpx.AsyncClient(timeout=10)
        self._owns_client = http_client is None

    async def notify(self, market: NewMarket) -> None:
        color = _PLATFORM_COLORS.get(market.platform, 0xFFFFFF)

        fields = [
            {"name": "💰 Price", "value": market.display_price, "inline": True},
        ]
        if market.volume is not None:
            fields.append(
                {"name": "📊 Volume", "value": f"{market.volume:,.0f}", "inline": True}
            )
        if market.category:
            fields.append(
                {"name": "🏷️ Category", "value": market.category, "inline": True}
            )

        embed = {
            "title": f"🆕 {market.platform.value.upper()}: {market.title}",
            "description": market.subtitle or "",
            "color": color,
            "fields": fields,
            "timestamp": market.detected_at.isoformat(),
        }
        if market.platform_url:
            embed["url"] = market.platform_url

        payload = {"embeds": [embed]}

        try:
            resp = await self._http.post(self._webhook_url, json=payload)
            resp.raise_for_status()
        except Exception as exc:
            logger.error("[discord] Failed to send webhook: %s", exc)

    async def close(self) -> None:
        if self._owns_client:
            await self._http.aclose()
