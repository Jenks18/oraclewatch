"""Shared data models for OracleWatch."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class Platform(str, Enum):
    KALSHI = "kalshi"
    POLYMARKET = "polymarket"


class MarketStatus(str, Enum):
    OPEN = "open"
    CLOSED = "closed"
    SETTLED = "settled"
    UNKNOWN = "unknown"


class NewMarket(BaseModel):
    """Represents a newly detected prediction market."""

    platform: Platform
    market_id: str = Field(description="Unique market identifier (ticker or ID)")
    title: str
    subtitle: str = ""
    url: str = ""
    status: MarketStatus = MarketStatus.OPEN
    created_at: Optional[datetime] = None
    close_time: Optional[datetime] = None
    category: str = ""

    # Price snapshot at detection time
    yes_price: Optional[float] = None
    no_price: Optional[float] = None
    volume: Optional[float] = None

    # Kalshi-specific
    event_ticker: str = ""

    # Polymarket-specific
    outcomes: List[str] = Field(default_factory=list)
    outcome_prices: List[float] = Field(default_factory=list)

    detected_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def display_price(self) -> str:
        if self.yes_price is not None:
            return f"Yes: {self.yes_price:.0%} / No: {self.no_price:.0%}" if self.no_price else f"Yes: {self.yes_price:.0%}"
        if self.outcome_prices:
            parts = [f"{o}: {p:.0%}" for o, p in zip(self.outcomes, self.outcome_prices)]
            return " | ".join(parts)
        return "N/A"

    @property
    def platform_url(self) -> str:
        if self.url:
            return self.url
        if self.platform == Platform.KALSHI:
            return f"https://kalshi.com/markets/{self.event_ticker}"
        return ""
