"""Binance Spot WebSocket Market Streams client.

Supports:
  - aggTrade:  real-time aggregated trades per symbol
  - !ticker@arr / !ticker_1h@arr: rolling-window stats for all symbols
  - Auto-reconnect, multi-stream subscription
  - Testnet + mainnet endpoints

Used by whales.py for real-time whale-buy detection (single aggTrade > $X).
"""
import os
import asyncio
import json
import logging
from collections import defaultdict
from typing import Callable, Optional
import websockets

log = logging.getLogger("gio.ws")

BINANCE_WS = {
    "main": "wss://stream.binance.com:9443/stream",
    "main_alt": "wss://stream.binance.com:443/stream",
    "testnet": "wss://stream.testnet.binance.vision/stream",
    "demo": "wss://demo-stream.binance.com:9443/stream",
}


class BinanceWS:
    def __init__(self, mode: str = "testnet"):
        self.url = BINANCE_WS.get(mode, BINANCE_WS["testnet"])
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.handlers: dict = defaultdict(list)  # stream -> [callback]
        self.running = False
        self.reconnect_delay = 5
        self.subscriptions: list = []  # streams currently subscribed

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.close()

    async def connect(self):
        log.info("connecting to %s", self.url)
        self.ws = await websockets.connect(self.url, ping_interval=30)
        self.running = True
        # resubscribe previous
        if self.subscriptions:
            await self.subscribe(self.subscriptions)

    async def close(self):
        self.running = False
        if self.ws:
            await self.ws.close()

    async def subscribe(self, streams: list):
        """Subscribe to one or more streams. Streams like 'btcusdt@aggTrade'."""
        if isinstance(streams, str):
            streams = [streams]
        msg = {"method": "SUBSCRIBE", "params": streams, "id": 1}
        if self.ws:
            await self.ws.send(json.dumps(msg))
        for s in streams:
            if s not in self.subscriptions:
                self.subscriptions.append(s)
        log.info("subscribed: %s", streams)

    def on(self, stream: str, callback: Callable):
        """Register callback for a stream."""
        self.handlers[stream].append(callback)

    async def listen(self):
        """Main loop. Dispatches messages to registered handlers."""
        while self.running:
            try:
                if not self.ws:
                    await self.connect()
                msg = await self.ws.recv()
                data = json.loads(msg)
                # subscription confirm: {"result":null,"id":1} - skip
                if "result" in data and data.get("id") is not None and "stream" not in data:
                    continue
                # combined stream: {"stream": "...", "data": {...}}
                if "stream" in data and "data" in data:
                    stream_name = data["stream"]
                    payload = data["data"]
                else:
                    payload = data
                    stream_name = None
                    # raw single-stream subscription - payload has no "stream" key
                    # use first subscribed stream
                    if self.subscriptions:
                        stream_name = self.subscriptions[0]
                if stream_name:
                    for cb in self.handlers.get(stream_name, []):
                        try:
                            await cb(payload)
                        except Exception as e:
                            log.exception("handler err: %s", e)
            except websockets.ConnectionClosed:
                log.warning("ws closed, reconnecting in %ss", self.reconnect_delay)
                await asyncio.sleep(self.reconnect_delay)
                await self.connect()
            except Exception as e:
                log.exception("ws err: %s", e)
                await asyncio.sleep(self.reconnect_delay)


# ============ convenience streamers ============

async def stream_aggtrade(symbol: str, callback: Callable, mode: str = "testnet"):
    """Stream aggTrade for one symbol. Callback receives parsed dict."""
    stream = f"{symbol.lower()}@aggTrade"

    async def _on(payload):
        await callback({
            "symbol": payload.get("s"),
            "price": float(payload.get("p", 0)),
            "qty": float(payload.get("q", 0)),
            "trade_id": payload.get("a"),
            "is_buyer_maker": payload.get("m"),  # true = sell aggressor
            "ts": payload.get("T"),
        })

    ws = BinanceWS(mode=mode)
    ws.on(stream, _on)
    await ws.connect()
    await ws.subscribe([stream])
    await ws.listen()


async def stream_all_tickers(callback: Callable, window: str = "1h",
                            mode: str = "testnet"):
    """Stream rolling-window stats for ALL symbols. High volume."""
    stream = f"!ticker_{window}@arr"

    async def _on(payload):
        # payload is a LIST of ticker dicts
        for t in payload:
            await callback({
                "symbol": t.get("s"),
                "price": float(t.get("c", 0)),
                "open": float(t.get("o", 0)),
                "high": float(t.get("h", 0)),
                "low": float(t.get("l", 0)),
                "volume": float(t.get("v", 0)),
                "quote_volume": float(t.get("q", 0)),
                "price_change_pct": float(t.get("P", 0)),
            })

    ws = BinanceWS(mode=mode)
    ws.on(stream, _on)
    await ws.connect()
    await ws.subscribe([stream])
    await ws.listen()


# ============ whale detector on top of aggTrade ============

class WhaleDetector:
    """Tracks large aggTrade events per symbol. Emits signal when thresholds met."""

    def __init__(self, min_usd: float = 100000, window_sec: int = 60):
        self.min_usd = min_usd
        self.window_sec = window_sec
        self.events: dict = defaultdict(list)  # symbol -> [(ts, price, qty, usd)]

    async def on_trade(self, trade: dict):
        sym = trade["symbol"]
        usd = trade["price"] * trade["qty"]
        if usd < self.min_usd:
            return
        ts = trade["ts"]
        # prune old
        self.events[sym] = [(t, p, q, u) for (t, p, q, u) in
                              self.events[sym] if ts - t < self.window_sec * 1000]
        self.events[sym].append((ts, trade["price"], trade["qty"], usd))
        # emit if >=3 whales in window
        if len(self.events[sym]) >= 3:
            total = sum(e[3] for e in self.events[sym])
            yield {
                "source": "binance_ws",
                "asset": sym,
                "n_trades": len(self.events[sym]),
                "total_usd": total,
                "ts": ts,
            }
            # reset window after emit
            self.events[sym] = []