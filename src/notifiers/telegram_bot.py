"""Telegram bot with self-subscribe via /start.

Users message the bot, it stores their chat_id, and they receive
new market alerts automatically. No admin setup needed per-user.

Commands:
  /start    - Subscribe to alerts
  /stop     - Unsubscribe
  /filter   - Set keyword filter (e.g. /filter crypto,election)
  /status   - Check subscription status
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

import httpx

from src.models import NewMarket, Platform
from src.notifiers.base import BaseNotifier
from src.storage.sqlite import MarketStore

logger = logging.getLogger("oraclewatch")

_PLATFORM_EMOJI = {
    Platform.KALSHI: "🔷",
    Platform.POLYMARKET: "🟣",
}

WELCOME_MSG = """🔮 <b>Welcome to OracleWatch!</b>

You're now subscribed to real-time prediction market alerts from <b>Kalshi</b> and <b>Polymarket</b>.

You'll get a message the second a new market goes live.

<b>Commands:</b>
/stop - Unsubscribe from alerts
/filter - Set keyword filters (e.g. <code>/filter crypto,election</code>)
/clear - Remove all filters (get everything)
/status - Check your subscription
/help - Show this message"""

STOP_MSG = """👋 You've been unsubscribed from OracleWatch alerts.

Send /start anytime to re-subscribe."""

HELP_MSG = WELCOME_MSG


def _matches_subscriber_filter(market: NewMarket, filter_keywords: str) -> bool:
    """Check if a market matches a subscriber's keyword filter."""
    if not filter_keywords or not filter_keywords.strip():
        return True
    keywords = [kw.strip().lower() for kw in filter_keywords.split(",") if kw.strip()]
    if not keywords:
        return True
    text = "{} {} {}".format(market.title, market.subtitle, market.category).lower()
    return any(kw in text for kw in keywords)


class TelegramBotNotifier(BaseNotifier):
    """Telegram bot that handles subscriptions and broadcasts alerts."""

    def __init__(
        self,
        bot_token: str,
        store: MarketStore,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self._bot_token = bot_token
        self._store = store
        self._http = http_client or httpx.AsyncClient(timeout=15)
        self._owns_client = http_client is None
        self._api = "https://api.telegram.org/bot{}".format(bot_token)
        self._last_update_id = 0
        self._polling = False

    # ── Sending messages ─────────────────────────────────────────────

    async def _send(self, chat_id: str, text: str) -> None:
        """Send an HTML message to a chat."""
        try:
            resp = await self._http.post(
                "{}/sendMessage".format(self._api),
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": False,
                },
            )
            if resp.status_code == 403:
                # User blocked the bot — deactivate
                logger.info("[telegram] User %s blocked bot, removing", chat_id)
                await self._store.remove_subscriber(chat_id)
            elif resp.status_code != 200:
                logger.warning("[telegram] Send failed (%s): %s", resp.status_code, resp.text[:200])
        except Exception as exc:
            logger.error("[telegram] Send error: %s", exc)

    def _format_market(self, market: NewMarket) -> str:
        """Format a market alert as HTML."""
        emoji = _PLATFORM_EMOJI.get(market.platform, "📢")
        lines = [
            "{} <b>NEW {} MARKET</b>".format(emoji, market.platform.value.upper()),
            "",
            "<b>{}</b>".format(_escape(market.title)),
        ]
        if market.subtitle:
            lines.append("<i>{}</i>".format(_escape(market.subtitle)))
        lines.append("💰 Price: {}".format(market.display_price))
        if market.volume is not None:
            lines.append("📊 Volume: {:,.0f}".format(market.volume))
        if market.category:
            lines.append("🏷️ {}".format(_escape(market.category)))
        if market.platform_url:
            lines.append('🔗 <a href="{}">View on {}</a>'.format(
                market.platform_url, market.platform.value.capitalize()
            ))
        return "\n".join(lines)

    # ── BaseNotifier interface ───────────────────────────────────────

    async def notify(self, market: NewMarket) -> None:
        """Send a market alert to all active subscribers."""
        subscribers = await self._store.get_active_subscribers()
        if not subscribers:
            return

        text = self._format_market(market)
        for sub in subscribers:
            if _matches_subscriber_filter(market, sub.get("filter_keywords", "")):
                await self._send(sub["chat_id"], text)
                await asyncio.sleep(0.05)  # Telegram rate limit: ~30 msg/sec

    async def notify_batch(self, markets: List[NewMarket]) -> None:
        """Send alerts for multiple markets."""
        for market in markets:
            await self.notify(market)

    async def close(self) -> None:
        self._polling = False
        if self._owns_client:
            await self._http.aclose()

    # ── Polling for commands ─────────────────────────────────────────

    async def start_polling(self) -> None:
        """Start polling for Telegram updates (commands from users)."""
        self._polling = True
        logger.info("[telegram] Bot polling started — users can /start to subscribe")

        # Get bot info
        try:
            resp = await self._http.get("{}/getMe".format(self._api))
            if resp.status_code == 200:
                data = resp.json()
                bot_name = data.get("result", {}).get("username", "unknown")
                logger.info("[telegram] Bot: @%s", bot_name)
        except Exception:
            pass

        while self._polling:
            try:
                await self._poll_updates()
            except Exception as exc:
                logger.error("[telegram] Poll error: %s", exc)
            await asyncio.sleep(1)

    async def _poll_updates(self) -> None:
        """Fetch and process new Telegram updates."""
        try:
            resp = await self._http.get(
                "{}/getUpdates".format(self._api),
                params={
                    "offset": self._last_update_id + 1,
                    "timeout": 5,
                    "allowed_updates": '["message"]',
                },
                timeout=15,
            )
            if resp.status_code != 200:
                return
            data = resp.json()
            updates = data.get("result", [])
        except (httpx.ReadTimeout, httpx.ConnectTimeout):
            return

        for update in updates:
            self._last_update_id = update.get("update_id", self._last_update_id)
            message = update.get("message", {})
            await self._handle_message(message)

    async def _handle_message(self, message: Dict[str, Any]) -> None:
        """Handle an incoming Telegram message."""
        text = message.get("text", "").strip()
        chat = message.get("chat", {})
        chat_id = str(chat.get("id", ""))
        user = message.get("from", {})
        username = user.get("username", "")
        first_name = user.get("first_name", "")

        if not chat_id or not text:
            return

        if text.startswith("/start"):
            await self._store.add_subscriber(chat_id, username, first_name)
            await self._send(chat_id, WELCOME_MSG)
            logger.info("[telegram] New subscriber: @%s (%s)", username, chat_id)

        elif text.startswith("/stop"):
            await self._store.remove_subscriber(chat_id)
            await self._send(chat_id, STOP_MSG)
            logger.info("[telegram] Unsubscribed: @%s (%s)", username, chat_id)

        elif text.startswith("/filter"):
            keywords = text.replace("/filter", "").strip()
            if keywords:
                await self._store.set_subscriber_filter(chat_id, keywords)
                await self._send(
                    chat_id,
                    "✅ Filter set! You'll only get alerts matching: <code>{}</code>\n\nSend /clear to remove filters.".format(
                        _escape(keywords)
                    ),
                )
            else:
                await self._send(
                    chat_id,
                    "Usage: <code>/filter keyword1,keyword2</code>\n\nExample: <code>/filter crypto,election,bitcoin</code>",
                )

        elif text.startswith("/clear"):
            await self._store.set_subscriber_filter(chat_id, "")
            await self._send(chat_id, "✅ Filters cleared — you'll receive all new market alerts.")

        elif text.startswith("/status"):
            subs = await self._store.get_active_subscribers()
            is_sub = any(s["chat_id"] == chat_id for s in subs)
            sub_data = next((s for s in subs if s["chat_id"] == chat_id), None)
            count = await self._store.get_market_count()
            sub_count = await self._store.get_subscriber_count()

            if is_sub:
                filt = sub_data.get("filter_keywords", "") if sub_data else ""
                filter_line = "🔍 Filter: <code>{}</code>".format(_escape(filt)) if filt else "🔍 Filter: <i>none (all markets)</i>"
                await self._send(
                    chat_id,
                    "✅ <b>You're subscribed!</b>\n\n{}\n📊 Markets tracked: {:,}\n👥 Total subscribers: {}".format(
                        filter_line, count, sub_count
                    ),
                )
            else:
                await self._send(chat_id, "❌ You're not subscribed. Send /start to subscribe.")

        elif text.startswith("/help"):
            await self._send(chat_id, HELP_MSG)


def _escape(text: str) -> str:
    """Escape HTML for Telegram."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
