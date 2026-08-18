# GIO Trading Bot

Telegram advisor for crypto/equity/FX. Multi-strategy (intraday/long-term/swing), multi-asset, structured recommendations.

## Stack
- Python 3.11+ (works on macOS / Linux / Windows)
- python-telegram-bot 21.x
- OpenRouter (free tier) for LLM
- CoinGecko (free) for prices
- DeBank (when wallet connected) + Archam (with API key) for on-chain data

## Quick start (macOS / Linux)
```bash
git clone git@github.com:guiboratto/gio-trading-bot.git
cd gio-trading-bot
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # fill TELEGRAM_BOT_TOKEN + OPENROUTER_API_KEY
python -m gio_trading_bot
```

## Get tokens
- Telegram: [@BotFather](https://t.me/BotFather) -> `/newbot`
- OpenRouter: [openrouter.ai](https://openrouter.ai/) -> free credits, then create key

## Commands
- `/start` - conversational advisor
- `/analyze BTC` - quick analysis
- `/tier` - freemium info
- `/cancel` - cancel dialog

## Monitization (roadmap)
- Free: 5 analyses/day
- Pro $9/mo via Telegram Stars (limitless, watchlists, alerts)
- Signal $0.50 (one-shot deep report)

## Files
- `bot.py` - Telegram handlers + ConversationHandler flow
- `advisor.py` - LLM prompt + OpenRouter client
- `market_data.py` - CoinGecko / DeBank / Archam clients
- `db.py` - SQLite (users, usage, history)