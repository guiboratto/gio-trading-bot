"""Archam API client (kept for optional Pro tier use).

If ARCHAM_API_KEY not set, all functions return empty/error gracefully.
Real-time signals come from whales.py (free stack).
"""
import os
import httpx

BASE = "https://api.archam.ai"


async def smart_money_top(limit: int = 50, chain: str = "eth") -> dict:
    key = os.environ.get("ARCHAM_API_KEY")
    if not key:
        return {"error": "ARCHAM_API_KEY not set", "wallets": []}
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(
                f"{BASE}/v1/smart-money/wallets",
                params={"limit": limit, "chain": chain},
                headers={"Authorization": f"Bearer {key}"},
            )
            r.raise_for_status()
            return r.json()
    except Exception as e:
        return {"error": str(e), "wallets": []}


async def wallet_pnl(wallet: str, chain: str = "eth") -> dict:
    key = os.environ.get("ARCHAM_API_KEY")
    if not key:
        return {"error": "ARCHAM_API_KEY not set"}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"{BASE}/v1/wallets/{wallet}/pnl",
                params={"chain": chain},
                headers={"Authorization": f"Bearer {key}"},
            )
            return r.json() if r.status_code == 200 else {"error": r.text}
    except Exception as e:
        return {"error": str(e)}


async def wallet_trades(wallet: str, chain: str = "eth", days: int = 7) -> dict:
    key = os.environ.get("ARCHAM_API_KEY")
    if not key:
        return {"error": "ARCHAM_API_KEY not set", "trades": []}
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(
                f"{BASE}/v1/wallets/{wallet}/trades",
                params={"chain": chain, "days": days},
                headers={"Authorization": f"Bearer {key}"},
            )
            return r.json() if r.status_code == 200 else {"error": r.text, "trades": []}
    except Exception as e:
        return {"error": str(e), "trades": []}