"""OracleWatch configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from typing import List, Optional

from dotenv import load_dotenv


def _bool(val: Optional[str]) -> bool:
    return str(val).lower() in ("true", "1", "yes")


def _keywords(val: Optional[str]) -> List[str]:
    if not val:
        return []
    return [kw.strip().lower() for kw in val.split(",") if kw.strip()]


@dataclass(frozen=True)
class Config:
    # Polling
    poll_interval_seconds: int = 5

    # Kalshi
    kalshi_enabled: bool = True
    kalshi_api_base_url: str = "https://api.elections.kalshi.com/trade-api/v2"

    # Polymarket
    polymarket_enabled: bool = True
    polymarket_gamma_api_url: str = "https://gamma-api.polymarket.com"

    # Notifications
    console_notifications: bool = True
    discord_enabled: bool = False
    discord_webhook_url: str = ""
    telegram_enabled: bool = False
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Web dashboard
    web_port: int = 8000

    # Filters
    filter_keywords: List[str] = field(default_factory=list)

    # Storage
    database_path: str = "oraclewatch.db"

    @classmethod
    def from_env(cls, env_path: Optional[str] = None) -> "Config":
        """Load configuration from .env file and environment variables."""
        if env_path:
            load_dotenv(env_path)
        else:
            load_dotenv()  # auto-find .env

        return cls(
            poll_interval_seconds=int(os.getenv("POLL_INTERVAL_SECONDS", "5")),
            kalshi_enabled=_bool(os.getenv("KALSHI_ENABLED", "true")),
            kalshi_api_base_url=os.getenv(
                "KALSHI_API_BASE_URL",
                "https://api.elections.kalshi.com/trade-api/v2",
            ),
            polymarket_enabled=_bool(os.getenv("POLYMARKET_ENABLED", "true")),
            polymarket_gamma_api_url=os.getenv(
                "POLYMARKET_GAMMA_API_URL",
                "https://gamma-api.polymarket.com",
            ),
            console_notifications=_bool(os.getenv("CONSOLE_NOTIFICATIONS", "true")),
            discord_enabled=_bool(os.getenv("DISCORD_ENABLED", "false")),
            discord_webhook_url=os.getenv("DISCORD_WEBHOOK_URL", ""),
            telegram_enabled=_bool(os.getenv("TELEGRAM_ENABLED", "false")),
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
            web_port=int(os.getenv("PORT", os.getenv("WEB_PORT", "8000"))),
            filter_keywords=_keywords(os.getenv("FILTER_KEYWORDS")),
            database_path=os.getenv("DATABASE_PATH", "oraclewatch.db"),
        )
