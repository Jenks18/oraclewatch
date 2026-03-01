# OracleWatch

Real-time monitoring and alerting infrastructure that notifies traders the second new prediction markets are deployed on **Kalshi** and **Polymarket**.

## Features

- **Dual-platform polling** — monitors both Kalshi and Polymarket APIs simultaneously
- **Instant alerts** — console, Discord webhook, and Telegram bot notifications
- **Smart deduplication** — SQLite-backed tracking ensures you never get duplicate alerts
- **Keyword filtering** — optionally filter alerts to only markets matching your interests
- **Seed on startup** — indexes existing markets on first run so you only get alerts for *new* ones
- **Async architecture** — fully async with `httpx` and `asyncio` for low-latency polling
- **Docker-ready** — deploy with a single `docker compose up`

## Quick Start

### 1. Install dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env with your notification preferences
```

### 3. Run

```bash
python -m src.main
```

Add `--verbose` / `-v` for debug logging.

## Configuration

All configuration is via environment variables (or `.env` file):

| Variable | Default | Description |
|---|---|---|
| `POLL_INTERVAL_SECONDS` | `30` | How often to check for new markets |
| `KALSHI_ENABLED` | `true` | Enable Kalshi polling |
| `POLYMARKET_ENABLED` | `true` | Enable Polymarket polling |
| `CONSOLE_NOTIFICATIONS` | `true` | Print alerts to terminal |
| `DISCORD_ENABLED` | `false` | Send Discord webhook alerts |
| `DISCORD_WEBHOOK_URL` | — | Discord webhook URL |
| `TELEGRAM_ENABLED` | `false` | Send Telegram bot alerts |
| `TELEGRAM_BOT_TOKEN` | — | Telegram bot token |
| `TELEGRAM_CHAT_ID` | — | Telegram chat/channel ID |
| `FILTER_KEYWORDS` | — | Comma-separated keywords to filter markets |
| `DATABASE_PATH` | `oraclewatch.db` | SQLite database path |

## Docker

```bash
cp .env.example .env
# Edit .env
docker compose up -d
```

## Architecture

```
src/
├── main.py              # Orchestrator & CLI entrypoint
├── config.py            # Environment-based configuration
├── models.py            # Shared data models (NewMarket, Platform)
├── pollers/
│   ├── base.py          # Abstract poller interface
│   ├── kalshi.py        # Kalshi API poller (GET /markets with min_created_ts)
│   └── polymarket.py    # Polymarket Gamma API poller
├── notifiers/
│   ├── base.py          # Abstract notifier interface
│   ├── console.py       # Rich terminal output
│   ├── discord.py       # Discord webhook embeds
│   └── telegram.py      # Telegram bot messages
└── storage/
    └── sqlite.py        # Async SQLite deduplication store
```

### How it works

1. **Startup** — Seeds the database with all currently-existing markets (no false alerts)
2. **Poll loop** — Every N seconds, queries both Kalshi and Polymarket APIs concurrently
3. **Dedup** — Checks each market against SQLite; only new ones pass through
4. **Filter** — Optionally filters by keyword
5. **Alert** — Sends to all enabled notification channels in parallel

## API Details

### Kalshi
- Uses `GET /trade-api/v2/markets` with `min_created_ts` for efficient incremental polling
- Supports cursor-based pagination for large result sets
- No authentication required for market data

### Polymarket
- Uses Gamma API (`gamma-api.polymarket.com`) — fully public, no auth
- Polls both `/events` (for event context) and `/markets` (for completeness)
- Deduplicates across both endpoints

## License

MIT
