"""DeBank API client - kept for Pro tier.

Real-time tracking is done via Etherscan V2 in whales.py (free).
"""
import os
import time
import httpx

BASE = "https://api.debank.com"


async def wallet_tx_history(wallet: str, chain: str = "eth", limit: int = 20,
                             days: int = 1) -> dict:
    key = os.environ.get("DEBANK_API_KEY", "")
    headers = {"User-Agent": "gio-whale-tracker/1.0"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(
                f"{BASE}/wallet/transaction_history",
                params={"id": wallet, "chain": chain, "limit": limit,
                        "start_time": int(time.time()) - days * 86400},
                headers=headers,
            )
            if r.status_code == 200:
                return {"txs": r.json().get("data", {}).get("history", [])}
            return {"error": f"HTTP {r.status_code}", "body": r.text[:200]}
    except Exception as e:
        return {"error": str(e), "txs": []}


async def wallet_net_worth(wallet: str, chain: str = "eth") -> dict:
    key = os.environ.get("DEBANK_API_KEY", "")
    headers = {"User-Agent": "gio-whale-tracker/1.0"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"{BASE}/wallet/net_worth",
                params={"id": wallet, "chain": chain},
                headers=headers,
            )
            return r.json() if r.status_code == 200 else {"error": r.text}
    except Exception as e:
        return {"error": str(e)}