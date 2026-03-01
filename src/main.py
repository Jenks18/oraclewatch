"""OracleWatch — main entrypoint and orchestrator.

Runs three systems concurrently:
  1. Market pollers (Kalshi + Polymarket) every N seconds
  2. Web dashboard (FastAPI + Uvicorn)
  3. Telegram bot (long-polling for /start subscriptions)
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from typing import List, Optional

import httpx
import uvicorn
from rich.console import Console
from rich.logging import RichHandler

from src.config import Config
from src.models import NewMarket
from src.notifiers.base import BaseNotifier
from src.notifiers.console import ConsoleNotifier
from src.notifiers.telegram_bot import TelegramBotNotifier
from src.pollers.base import BasePoller
from src.pollers.kalshi import KalshiPoller
from src.pollers.polymarket import PolymarketPoller
from src.storage.sqlite import MarketStore
from src.web.app import create_app

console = Console()
logger = logging.getLogger("oraclewatch")

BANNER = r"""
   ___                 _    __        __    _       _
  / _ \ _ __ __ _  ___| | __\ \      / /_ _| |_ ___| |__
 | | | | '__/ _` |/ __| |/ _ \ \ /\ / / _` | __/ __| '_ \
 | |_| | | | (_| | (__| |  __/\ V  V / (_| | || (__| | | |
  \___/|_|  \__,_|\___|_|\___| \_/\_/ \__,_|\__\___|_| |_|

  Real-time prediction market monitor
"""


def _setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(rich_tracebacks=True, show_path=False)],
    )
    # Quiet down noisy loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def _matches_filters(market: NewMarket, keywords: List[str]) -> bool:
    """Check if a market matches the keyword filters. Empty list = match all."""
    if not keywords:
        return True
    text = "{} {} {}".format(market.title, market.subtitle, market.category).lower()
    return any(kw in text for kw in keywords)


class OracleWatch:
    """Main orchestrator that runs polling, web, and Telegram concurrently."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._store = MarketStore(config.database_path)
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(30, connect=10),
            headers={"User-Agent": "OracleWatch/0.1"},
            follow_redirects=True,
        )
        self._running = False
        self._telegram_bot = None  # type: Optional[TelegramBotNotifier]
        self._telegram_bot_username = ""
        self._pollers = self._build_pollers()
        self._notifiers = self._build_notifiers()

    def _build_pollers(self) -> List[BasePoller]:
        pollers = []  # type: List[BasePoller]
        if self._config.kalshi_enabled:
            pollers.append(KalshiPoller(self._store, self._http, base_url=self._config.kalshi_api_base_url))
        if self._config.polymarket_enabled:
            pollers.append(PolymarketPoller(self._store, self._http, gamma_api_url=self._config.polymarket_gamma_api_url))
        return pollers

    def _build_notifiers(self) -> List[BaseNotifier]:
        notifiers = []  # type: List[BaseNotifier]
        if self._config.console_notifications:
            notifiers.append(ConsoleNotifier())
        if self._config.telegram_bot_token:
            bot = TelegramBotNotifier(self._config.telegram_bot_token, self._store, self._http)
            self._telegram_bot = bot
            notifiers.append(bot)
        return notifiers

    async def start(self) -> None:
        """Boot everything up."""
        await self._store.connect()
        self._running = True

        # Resolve Telegram bot username for the dashboard link
        if self._telegram_bot and self._config.telegram_bot_token:
            try:
                resp = await self._http.get(
                    "https://api.telegram.org/bot{}/getMe".format(self._config.telegram_bot_token)
                )
                if resp.status_code == 200:
                    self._telegram_bot_username = resp.json().get("result", {}).get("username", "")
            except Exception:
                pass

        console.print(BANNER, style="bold cyan")

        info_parts = [
            "Polling every [bold]{}s[/bold]".format(self._config.poll_interval_seconds),
            "Pollers: [bold]{}[/bold]".format(len(self._pollers)),
            "Notifiers: [bold]{}[/bold]".format(len(self._notifiers)),
            "Dashboard: [bold]http://localhost:{}[/bold]".format(self._config.web_port),
        ]
        if self._telegram_bot_username:
            info_parts.append("Telegram: [bold]@{}[/bold]".format(self._telegram_bot_username))
        console.print("  " + " | ".join(info_parts) + "\n", style="dim")

        # Seed pass
        logger.info("Running initial market scan (seeding database)...")
        await self._seed_pass()
        total = await self._store.seen_count()
        logger.info("Seed complete — %d markets indexed. Starting live monitoring...", total)

        # Launch all tasks concurrently
        tasks = [
            asyncio.ensure_future(self._poll_loop()),
            asyncio.ensure_future(self._run_web()),
        ]
        if self._telegram_bot:
            tasks.append(asyncio.ensure_future(self._telegram_bot.start_polling()))

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            pass

    async def _seed_pass(self) -> None:
        """First pass: mark all existing markets as seen without alerting."""
        for poller in self._pollers:
            try:
                markets = await poller.fetch_recent_markets()
                await self._store.mark_seen_batch(markets)
                await self._store.store_markets_batch(markets)
                logger.info("[%s] Seeded %d existing markets", poller.platform.value, len(markets))
            except Exception as exc:
                logger.error("[%s] Seed failed: %s", poller.platform.value, exc, exc_info=True)

    async def _poll_loop(self) -> None:
        """Main polling loop."""
        while self._running:
            try:
                await self._poll_cycle()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Poll cycle error: %s", exc, exc_info=True)
            await asyncio.sleep(self._config.poll_interval_seconds)

    async def _poll_cycle(self) -> None:
        """One polling cycle across all pollers."""
        all_new = []  # type: List[NewMarket]

        results = await asyncio.gather(
            *[poller.poll() for poller in self._pollers],
            return_exceptions=True,
        )

        for result in results:
            if isinstance(result, Exception):
                logger.error("Poller error: %s", result)
            elif isinstance(result, list):
                all_new.extend(result)

        # Apply keyword filters
        filtered = [m for m in all_new if _matches_filters(m, self._config.filter_keywords)]

        # Store full market data for the dashboard
        if filtered:
            await self._store.store_markets_batch(filtered)

        # Send notifications
        if filtered:
            logger.info("🚨 %d new market(s) detected!", len(filtered))
            for notifier in self._notifiers:
                try:
                    await notifier.notify_batch(filtered)
                except Exception as exc:
                    logger.error("Notifier error: %s", exc)

    async def _run_web(self) -> None:
        """Run the FastAPI web dashboard."""
        app = create_app(self._store, self._telegram_bot_username)
        config = uvicorn.Config(
            app,
            host="0.0.0.0",
            port=self._config.web_port,
            log_level="warning",
        )
        server = uvicorn.Server(config)
        await server.serve()

    async def stop(self) -> None:
        """Gracefully shut down."""
        self._running = False
        logger.info("Shutting down OracleWatch...")
        for notifier in self._notifiers:
            await notifier.close()
        await self._store.close()
        await self._http.aclose()
        logger.info("Goodbye! 👋")


async def async_main(verbose: bool = False) -> None:
    """Async entrypoint."""
    _setup_logging(verbose)
    config = Config.from_env()
    watcher = OracleWatch(config)

    loop = asyncio.get_running_loop()

    def _signal_handler():
        asyncio.ensure_future(watcher.stop())

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    try:
        await watcher.start()
    finally:
        await watcher.stop()


def cli_entry() -> None:
    """CLI entrypoint."""
    verbose = "--verbose" in sys.argv or "-v" in sys.argv
    try:
        asyncio.run(async_main(verbose))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    cli_entry()
