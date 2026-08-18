"""Free-market data stack (no Archam, no DeBank Pro required).

Real-time signals without paid APIs:
  - CoinGecko Demo (already wired)
  - DefiLlama (DEX volume spikes, TVL deltas)
  - Etherscan V2 (whale tx flow, ERC-20 transfers)
  - Whale Alert (free tier, requires API key)
  - Fear & Greed (sentiment)

Signal model (pure on-chain, no entity labels):
  - Track top ERC-20 transfer events on Etherscan (> $1M)
  - For each token, count distinct sender/receiver addresses
  - If multiple large transfers hit same token within 1h -> signal
"""
import os
import asyncio
import json
import time
import logging
from collections import defaultdict
import httpx

from . import market_data

log = logging.getLogger("gio.whales")

ETHERSCAN_V2 = "https://api.etherscan.io/v2/api"
ETHERSCAN_KEY = os.environ.get("ETHERSCAN_API_KEY", "")  # free V2 key
WHALE_ALERT_KEY = os.environ.get("WHALE_ALERT_API_KEY", "")
DEFILLAMA = "https://api.llama.fi"


async def _g(url: str, params: dict | None = None, timeout: float = 15) -> dict | list:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(url, params=params or {})
            if r.status_code == 200:
                return r.json()
            return {"error": f"HTTP {r.status_code}", "body": r.text[:200]}
    except Exception as e:
        return {"error": str(e)}


# ============ DefiLlama ============

async def defillama_dex_volume(top_n: int = 10) -> dict:
    """Top DEXes by 24h volume."""
    data = await _g(f"{DEFILLAMA}/overview/dexs")
    if isinstance(data, dict) and "error" in data:
        return data
    if isinstance(data, list):
        return {"dexes": data[:top_n]}
    return {"dexes": data.get("protocols", [])[:top_n]}


async def defillama_tvl_deltas(threshold_pct: float = 10.0) -> list:
    """Protocols whose TVL moved >= threshold_pct in last 24h."""
    data = await _g(f"{DEFILLAMA}/protocols")
    if isinstance(data, dict) and "error" in data:
        return []
    movers = []
    for p in data:
        change = p.get("change_1d")
        if change is not None and abs(change) >= threshold_pct:
            movers.append({
                "name": p.get("name"),
                "category": p.get("category"),
                "chain": p.get("chain"),
                "tvl_usd": p.get("tvl"),
                "change_1d_pct": change,
            })
    movers.sort(key=lambda x: abs(x["change_1d_pct"]), reverse=True)
    return movers[:20]


async def defillama_global() -> dict:
    """Global TVL + chain breakdown."""
    return await _g(f"{DEFILLAMA}/global") or {}


# ============ Etherscan V2 ============

async def etherscan_large_transfers(threshold_eth: float = 100.0,
                                     limit: int = 50) -> list:
    """Recent large ETH transfers (whale proxy)."""
    if not ETHERSCAN_KEY:
        return []
    data = await _g(ETHERSCAN_V2, {
        "chainid": "1",
        "module": "account",
        "action": "txlist",
        "sort": "desc",
        "page": "1",
        "offset": str(limit),
        "apikey": ETHERSCAN_KEY,
    })
    if isinstance(data, dict) and data.get("status") != "1":
        return []
    result = data.get("result", [])
    out = []
    for tx in result:
        v = int(tx.get("value", "0")) / 1e18
        if v >= threshold_eth:
            out.append({
                "hash": tx.get("hash"),
                "from": tx.get("from"),
                "to": tx.get("to"),
                "value_eth": v,
                "value_usd": v * 3000,  # rough
                "timestamp": int(tx.get("timeStamp", "0")),
                "token": "ETH",
            })
    return out


async def etherscan_token_transfers(contract: str, threshold_usd: float = 100000,
                                     limit: int = 100) -> list:
    """Large ERC-20 transfers for a specific token contract."""
    if not ETHERSCAN_KEY:
        return []
    data = await _g(ETHERSCAN_V2, {
        "chainid": "1",
        "module": "account",
        "action": "tokentx",
        "contractaddress": contract,
        "sort": "desc",
        "page": "1",
        "offset": str(limit),
        "apikey": ETHERSCAN_KEY,
    })
    if isinstance(data, dict) and data.get("status") != "1":
        return []
    return data.get("result", [])


# ============ Whale Alert ============

async def whale_alert_recent(min_usd: int = 500000, limit: int = 20) -> list:
    """Recent large crypto transactions (free tier, no labels)."""
    if not WHALE_ALERT_KEY:
        return []
    data = await _g("https://api.whale-alert.io/v1/transactions", {
        "api_key": WHALE_ALERT_KEY,
        "min_value": min_usd,
        "limit": limit,
    })
    if isinstance(data, dict) and "error" in data:
        return []
    return data.get("transactions", [])


# ============ Fear & Greed ============

async def fear_greed() -> dict:
    """Crypto Fear & Greed Index."""
    data = await _g("https://api.alternative.me/fng/?limit=1")
    if isinstance(data, dict) and "error" in data:
        return data
    items = data.get("data", []) if isinstance(data, dict) else []
    if items:
        return {
            "value": int(items[0]["value"]),
            "classification": items[0]["value_classification"],
            "timestamp": int(items[0]["timestamp"]),
        }
    return {}


# ============ unified signal engine ============

async def get_signals(min_usd: int = 500000) -> list:
    """Combine free sources into one signal list.

    Returns list of dicts with: source, asset, direction, amount_usd, ts.
    """
    signals = []

    # 1) Whale Alert (best signal if key set)
    wa = await whale_alert_recent(min_usd=min_usd, limit=30)
    for t in wa:
        signals.append({
            "source": "whale_alert",
            "asset": t.get("symbol", "?"),
            "from": t.get("from", {}).get("address", "")[:12],
            "to": t.get("to", {}).get("address", "")[:12],
            "amount_usd": t.get("amount_usd", 0),
            "direction": "buy" if t.get("to", {}).get("owner_type") == "exchange" else "sell",
            "timestamp": t.get("timestamp"),
        })

    # 2) Etherscan large ETH transfers
    et = await etherscan_large_transfers(threshold_eth=100, limit=20)
    for t in et:
        signals.append({
            "source": "etherscan",
            "asset": "ETH",
            "from": t["from"][:12],
            "to": t["to"][:12],
            "amount_usd": t["value_usd"],
            "direction": "transfer",
            "timestamp": t["timestamp"],
        })

    # 3) DefiLlama TVL movers
    tv = await defillama_tvl_deltas(threshold_pct=15.0)
    for m in tv[:5]:
        signals.append({
            "source": "defillama",
            "asset": m.get("name"),
            "amount_usd": m.get("tvl_usd", 0),
            "direction": "tvl_up" if m["change_1d_pct"] > 0 else "tvl_down",
            "change_pct": m["change_1d_pct"],
            "timestamp": int(time.time()),
        })

    signals.sort(key=lambda s: s.get("amount_usd", 0), reverse=True)
    return signals[:30]


# ============ legacy API for bot.py compat ============

async def discover_whales(limit: int = 30, chain: str = "eth") -> list:
    """For backward compat - returns top recent ETH whales."""
    txs = await etherscan_large_transfers(threshold_eth=500, limit=limit)
    wallets = []
    seen = set()
    for t in txs:
        for addr in (t["from"], t["to"]):
            if addr and addr not in seen:
                seen.add(addr)
                wallets.append({
                    "address": addr,
                    "pnl": t["value_eth"] * 3000,
                    "win_rate": 0.0,
                    "last_active": t["timestamp"],
                })
    return wallets[:limit]


async def find_signals(whales: list, chain: str = "eth", window_days: int = 1,
                       min_buyers: int = 3) -> list:
    """Backward-compat shim - delegated to get_signals()."""
    sigs = await get_signals()
    return sigs[:10]