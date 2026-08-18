"""CoinGecko Demo/Pro market data client.

Demo key (free for testing): root URL = https://api.coingecko.com/api/v3
Pro key (paid): root URL    = https://pro-api.coingecko.com/api/v3

We auto-detect: if the key starts with 'CG-' (Demo), use api.* endpoint.
"""
import os
import time
import httpx

KEY = os.environ.get("COINGECKO_API_KEY", "")

# Demo keys start with "CG-", Pro keys don't
IS_DEMO = KEY.startswith("CG-")
BASE = "https://api.coingecko.com/api/v3" if IS_DEMO else "https://pro-api.coingecko.com/api/v3"

HEADERS = {"User-Agent": "gio-whale-tracker/1.0"}
# Demo API: pass key as query param `x_cg_demo_api_key`
# Pro API: pass key as header `x-cg-pro-api-key`
if KEY and IS_DEMO:
    HEADERS["x_cg_demo_api_key"] = KEY
elif KEY:
    HEADERS["x-cg-pro-api-key"] = KEY

# local symbol -> CoinGecko coin id
CG_MAP = {
    "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "USDT": "tether",
    "BNB": "binancecoin", "XRP": "ripple", "ADA": "cardano", "DOGE": "dogecoin",
    "TON": "the-open-network", "TRX": "tron", "AVAX": "avalanche-2",
    "MATIC": "matic-network", "LINK": "chainlink", "DOT": "polkadot",
    "LTC": "litecoin", "BCH": "bitcoin-cash", "UNI": "uniswap",
    "AAVE": "aave", "ARB": "arbitrum", "OP": "optimism",
}


async def _g(path: str, params: dict | None = None) -> dict:
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(f"{BASE}{path}", params=params or {}, headers=HEADERS)
            if r.status_code == 200:
                return r.json()
            return {"error": f"HTTP {r.status_code}", "body": r.text[:300]}
    except Exception as e:
        return {"error": str(e)}


async def price(symbol: str, vs: str = "usd") -> dict:
    cid = CG_MAP.get(symbol.upper(), symbol.lower())
    return await _g(
        "/simple/price",
        {"ids": cid, "vs_currencies": vs,
         "include_24hr_change": "true", "include_24hr_vol": "true",
         "include_market_cap": "true"},
    )


async def markets(symbol: str, vs: str = "usd", days: int = 7) -> dict:
    """Full market data: price, mcap, supply, ATH, change %, sparkline."""
    cid = CG_MAP.get(symbol.upper(), symbol.lower())
    return await _g(
        "/coins/markets",
        {"vs_currency": vs, "ids": cid,
         "sparkline": "true", "price_change_percentage": f"{days}d,14d,30d"},
    )


async def ohlc(symbol: str, vs: str = "usd", days: int = 1) -> dict:
    """OHLC candlestick. Days: 1/7/14/30/90/180/365."""
    cid = CG_MAP.get(symbol.upper(), symbol.lower())
    return await _g(f"/coins/{cid}/ohlc", {"vs_currency": vs, "days": days})


async def history(symbol: str, vs: str = "usd", days: int = 7) -> dict:
    """Historical price chart."""
    cid = CG_MAP.get(symbol.upper(), symbol.lower())
    return await _g(
        f"/coins/{cid}/market_chart",
        {"vs_currency": vs, "days": days, "interval": "hourly" if days <= 7 else "daily"},
    )


async def trending() -> dict:
    """Top trending coins (Pro only — high signal)."""
    return await _g("/search/trending")


async def global_data() -> dict:
    """Global market cap, volume, BTC dominance."""
    return await _g("/global")


async def top_gainers(limit: int = 10, vs: str = "usd") -> dict:
    """Top gainers 24h."""
    return await _g(
        "/coins/markets",
        {"vs_currency": vs, "order": "gecko_desc",
         "per_page": limit, "page": 1,
         "price_change_percentage": "24h", "sparkline": "false"},
    )


async def top_by_volume(limit: int = 10, vs: str = "usd") -> dict:
    """Top by 24h volume."""
    return await _g(
        "/coins/markets",
        {"vs_currency": vs, "order": "volume_desc",
         "per_page": limit, "page": 1, "sparkline": "false"},
    )


def coingecko(symbol: str) -> dict:
    """Back-compat shim used by bot.py — returns basic price dict."""
    import json as _json
    import urllib.request
    cid = CG_MAP.get(symbol.upper(), symbol.lower())
    url = f"{BASE}/simple/price?ids={cid}&vs_currencies=usd&include_24hr_change=true"
    if KEY and IS_DEMO:
        url += f"&x_cg_demo_api_key={KEY}"
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return _json.loads(r.read())
    except Exception as e:
        return {"error": str(e)}