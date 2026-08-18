"""Smoke test: DB init, env vars, CoinGecko Pro live API."""
import os
import sys
import asyncio
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()

print("TELEGRAM_BOT_TOKEN:", "OK" if os.environ.get("TELEGRAM_BOT_TOKEN") else "MISSING")
print("COINGECKO_API_KEY:", "OK" if os.environ.get("COINGECKO_API_KEY") else "MISSING")
print("OPENROUTER_API_KEY:", "OK" if os.environ.get("OPENROUTER_API_KEY") else "(empty)")
print("ARCHAM_API_KEY:", "OK" if os.environ.get("ARCHAM_API_KEY") else "(empty)")

from gio_trading_bot import db, market_data
db.init_db()
u = db.get_user(123)
print("DB OK, user 123 tier =", u["tier"])

async def t():
    print("\n--- CoinGecko Pro live ---")
    p = await market_data.price("BTC")
    print("BTC price:", p)
    m = await market_data.markets("ETH", days=7)
    if isinstance(m, list) and m:
        e = m[0]
        print(f"ETH: ${e.get('current_price')} cap=${e.get('market_cap'):,.0f} "
              f"24h={e.get('price_change_percentage_24h'):.2f}%")
    tr = await market_data.trending()
    if "coins" in tr:
        print("Trending top-3:", [c["item"]["symbol"] for c in tr["coins"][:3]])
    g = await market_data.global_data()
    if "data" in g:
        d = g["data"]
        print(f"Global: mcap=${d['total_market_cap']['usd']:,.0f}  "
              f"BTC dominance={d['market_cap_percentage']['btc']:.1f}%")

asyncio.run(t())
print("\nSMOKE OK")