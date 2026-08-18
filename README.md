# GIO Trading Bot — Whale Tracker

Telegram bot that tracks top-30 crypto whales (Archam) and emits real-time signals when 3+ whales buy the same token. DeBank provides on-chain confirmation.

## Tiers
- **Free** — 3 delayed signals/day, basic `/analyze`, no watchlist
- **Pro $49/mo** — real-time signals, watchlist alerts, full analyze
- **Whale $199/mo** — full whales list + PnL + copy-trade alerts (DM-only)

## Stack
- Python 3.11+ (macOS / Linux / Windows)
- `python-telegram-bot` 21.x
- OpenRouter (free tier) for AI advisor
- Archam (`api.archam.ai`) — whale discovery
- DeBank (`api.debank.com`) — on-chain confirmation

## Quick start
```bash
git clone git@github.com:guiboratto/gio-trading-bot.git
cd gio-trading-bot
cp .env.example .env  # fill TELEGRAM_BOT_TOKEN, OPENROUTER_API_KEY, ARCHAM_API_KEY, DEBANK_API_KEY
pip3 install -r requirements.txt
python3 -m gio_trading_bot
```

## Get tokens
- Telegram: [@BotFather](https://t.me/BotFather) → `/newbot`
- OpenRouter: [openrouter.ai](https://openrouter.ai/) → free credits
- Archam: [archam.ai](https://archam.ai/) → API key
- DeBank: [docs.cloud.debank.com](https://docs.cloud.debank.com/en/readme/api-pro-reference) → Pro API access

## Commands
- `/start` — main menu
- `/whales` — top 20 whale wallets
- `/signals` — tokens bought by 3+ whales (24h)
- `/track <TICKER>` — watchlist (Pro only)
- `/analyze <TICKER>` — one-shot AI advisor
- `/tier` — subscription info

## Files
- `bot.py` — Telegram handlers + background monitor
- `advisor.py` — LLM via OpenRouter
- `whales.py` — Archam discovery + signal detection
- `archam.py` — Archam API client
- `debank.py` — DeBank API client
- `market_data.py` — CoinGecko prices
- `db.py` — SQLite (users, usage, history)

## Roadmap
- Telegram Stars payments (Pro / Whale / Signal)
- Watchlist per-user table
- Real-time push notifications
- Web dashboard (Streamlit)