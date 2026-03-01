"""Abstract base notifier."""

from __future__ import annotations

from abc import ABC, abstractmethod

from src.models import NewMarket


class BaseNotifier(ABC):
    """All notifiers implement this interface."""

    @abstractmethod
    async def notify(self, market: NewMarket) -> None:
        """Send an alert for a single new market."""
        ...

    async def notify_batch(self, markets: list[NewMarket]) -> None:
        """Send alerts for multiple markets. Default: iterate one-by-one."""
        for market in markets:
            await self.notify(market)

    @abstractmethod
    async def close(self) -> None:
        """Clean up resources."""
        ...
