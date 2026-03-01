"""Rich console notifier – prints formatted alerts to the terminal."""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from src.models import NewMarket, Platform
from src.notifiers.base import BaseNotifier

_PLATFORM_COLORS = {
    Platform.KALSHI: "cyan",
    Platform.POLYMARKET: "magenta",
}

_PLATFORM_EMOJI = {
    Platform.KALSHI: "🔷",
    Platform.POLYMARKET: "🟣",
}


class ConsoleNotifier(BaseNotifier):
    """Prints new market alerts to the terminal with rich formatting."""

    def __init__(self) -> None:
        self._console = Console()

    async def notify(self, market: NewMarket) -> None:
        emoji = _PLATFORM_EMOJI.get(market.platform, "📢")
        color = _PLATFORM_COLORS.get(market.platform, "white")

        lines = [
            f"[bold]{market.title}[/bold]",
        ]
        if market.subtitle:
            lines.append(f"[dim]{market.subtitle}[/dim]")
        lines.append(f"💰 Price: {market.display_price}")
        if market.volume is not None:
            lines.append(f"📊 Volume: {market.volume:,.0f}")
        if market.category:
            lines.append(f"🏷️  Category: {market.category}")
        if market.platform_url:
            lines.append(f"🔗 {market.platform_url}")

        body = "\n".join(lines)
        title = f"{emoji} NEW {market.platform.value.upper()} MARKET"

        self._console.print(
            Panel(body, title=title, border_style=color, padding=(0, 1))
        )

    async def close(self) -> None:
        pass
