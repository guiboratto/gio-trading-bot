"""Smoke test: DB init, env vars present, syntax check."""
import os
import sys
import asyncio
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()

assert os.environ.get("TELEGRAM_BOT_TOKEN"), "TELEGRAM_BOT_TOKEN missing"
print("TELEGRAM_BOT_TOKEN: OK (", len(os.environ['TELEGRAM_BOT_TOKEN']), "chars)")

from gio_trading_bot import db, market_data, advisor
db.init_db()
u = db.get_user(123)
print("DB init OK, user 123 tier =", u["tier"])

if not os.environ.get("OPENROUTER_API_KEY"):
    print("OPENROUTER_API_KEY empty - skipping live LLM call")
else:
    print("OR key present, model:", os.environ.get("OPENROUTER_MODEL"))

async def t():
    cg = await market_data.coingecko("BTC")
    print("CoinGecko BTC:", cg)
asyncio.run(t())
print("SMOKE OK")