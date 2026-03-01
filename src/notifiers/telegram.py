"""Telegram bot notifier – sends formatted messages to a chat/channel."""

from __future__ import annotations

import logging

import httpx

from src.models import NewMarket, Platform
from src.notifiers.base import BaseNotifier

logger = logging.getLogger("oraclewatch")

_PLATFORM_EMOJI = {
    Platform.KALSHI: "🔷",
    Platform.POLYMARKET: "🟣",
}


class TelegramNotifier(BaseNotifier):
    """Send new-market alerts via the Telegram Bot API."""

    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._http = http_client or httpx.AsyncClient(timeout=10)
        self._owns_client = http_client is None
        self._api_base = f"https://api.telegram.org/bot{bot_token}"

    async def notify(self, market: NewMarket) -> None:
        emoji = _PLATFORM_EMOJI.get(market.platform, "📢")

        lines = [
            f"{emoji} <b>NEW {market.platform.value.upper()} MARKET</b>",
            "",
            f"<b>{self._escape(market.title)}</b>",
        ]
        if market.subtitle:
            lines.append(f"<i>{self._escape(market.subtitle)}</i>")
        lines.append(f"💰 Price: {market.display_price}")
        if market.volume is not None:
            lines.append(f"📊 Volume: {market.volume:,.0f}")
        if market.category:
            lines.append(f"🏷️ Category: {self._escape(market.category)}")
        if market.platform_url:
            lines.append(f'🔗 <a href="{market.platform_url}">View Market</a>')

        text = "\n".join(lines)

        payload = {
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        }

        try:
            resp = await self._http.post(f"{self._api_base}/sendMessage", json=payload)
            resp.raise_for_status()
        except Exception as exc:
            logger.error("[telegram] Failed to send message: %s", exc)

    @staticmethod
    def _escape(text: str) -> str:
        """Escape HTML special characters for Telegram."""
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    async def close(self) -> None:
        if self._owns_client:
            await self._http.aclose()
