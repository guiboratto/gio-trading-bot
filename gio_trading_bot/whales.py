"""Whale tracker: discovery, monitoring, signal detection."""
import asyncio
import json
import time
import logging
from collections import defaultdict
from . import archam, debank

log = logging.getLogger("gio.whales")


async def discover_whales(limit: int = 30, chain: str = "eth") -> list:
    """Pull top smart-money wallets from Archam."""
    res = await archam.smart_money_top(limit=limit, chain=chain)
    if "error" in res:
        log.warning("Archam whales error: %s", res["error"])
        return []
    return res.get("wallets", res.get("data", []))


async def get_token_buys(wallet: str, chain: str, days: int = 1) -> list:
    """Return list of tokens bought by wallet in last `days`."""
    res = await archam.wallet_trades(wallet, chain=chain, days=days)
    if "error" in res:
        return []
    trades = res.get("trades", res.get("data", []))
    buys = []
    for t in trades:
        side = (t.get("side") or t.get("action") or "").lower()
        if side in ("buy", "b"):
            token = t.get("token") or t.get("symbol") or t.get("address")
            if token:
                buys.append(token)
    return buys


async def find_signals(
    whales: list, chain: str = "eth", window_days: int = 1, min_buyers: int = 3
) -> list:
    """For each token bought by >=min_buyers whales in window, emit a signal."""
    token_buyers = defaultdict(set)
    for w in whales:
        addr = w.get("address") or w.get("wallet") or w.get("id")
        if not addr:
            continue
        buys = await get_token_buys(addr, chain=chain, days=window_days)
        for t in buys:
            token_buyers[t].add(addr)

    signals = []
    for token, buyers in token_buyers.items():
        if len(buyers) >= min_buyers:
            total_pnl = sum(
                w.get("pnl", 0) for w in whales
                if (w.get("address") or w.get("wallet") or w.get("id")) in buyers
            )
            signals.append({
                "token": token,
                "chain": chain,
                "buyers": list(buyers),
                "n_buyers": len(buyers),
                "total_pnl_usd": total_pnl,
                "detected_at": int(time.time()),
            })
    return signals


async def monitor_loop(callback, interval_sec: int = 300):
    """Run forever: discover whales, find signals, push to callback(signals).

    callback: async def cb(signals: list) -> None
    """
    chain = "eth"
    while True:
        try:
            whales = await discover_whales(limit=30, chain=chain)
            log.info("whales=%d", len(whales))
            if whales:
                signals = await find_signals(whales, chain=chain)
                log.info("signals=%d", len(signals))
                if signals:
                    await callback(signals)
        except Exception as e:
            log.exception("monitor_loop err: %s", e)
        await asyncio.sleep(interval_sec)