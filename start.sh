#!/bin/bash
set -e
echo "GIO Trading Bot - setup"
if [ ! -f .env ]; then
  echo "Create .env from template..."
  cp .env.example .env
  echo ""
  echo "!!! FILL .env with:"
  echo "   TELEGRAM_BOT_TOKEN (from @BotFather)"
  echo "   OPENROUTER_API_KEY (from openrouter.ai)"
  echo ""
  exit 1
fi
pip3 install -q -r requirements.txt
exec python3 -m gio_trading_bot
