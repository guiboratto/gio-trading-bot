"""Free market data clients."""
import os
import httpx

CG_MAP = {
    "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "USDT": "tether",
    "BNB": "binancecoin", "XRP": "ripple", "ADA": "cardano", "DOGE": "dogecoin",
    "TON": "the-open-network", "TRX": "tron", "AVAX": "avalanche-2",
}


async def coingecko(symbol: str) -> dict:
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={
                    "ids": CG_MAP.get(symbol.upper(), symbol.lower()),
                    "vs_currencies": "usd",
                    "include_24hr_change": "true",
                    "include_24hr_vol": "true",
                },
                headers={"User-Agent": "gio-trading-bot/1.0"},
            )
            r.raise_for_status()
            return r.json()
    except Exception as e:
        return {"error": str(e)}


async def debank(symbol: str) -> dict:
    """DeBank requires a wallet address; placeholder until user connects wallet."""
    return {"note": "DeBank needs wallet address (not collected).", "symbol": symbol}


async def archam(symbol: str) -> dict:
    key = os.environ.get("ARCHAM_API_KEY")
    if not key:
        return {"note": "ARCHAM_API_KEY not set.", "symbol": symbol}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                "https://api.archam.ai/v1/tokens/info",
                params={"symbol": symbol},
                headers={"Authorization": f"Bearer {key}"},
            )
            return r.json() if r.status_code == 200 else {"error": r.text}
    except Exception as e:
        return {"error": str(e)}