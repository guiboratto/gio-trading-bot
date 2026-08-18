"""FastAPI Mini App backend.

Endpoints (all return JSON):
  GET  /api/me?uid=<telegram_user_id> -> user info
  GET  /api/portfolio?uid=<id> -> PnL
  GET  /api/watchlist?uid=<id>
  POST /api/watch?uid=<id>&symbol=BTC -> add
  DELETE /api/watch?uid=<id>&symbol=BTC
  GET  /api/trades?uid=<id>
  POST /api/trade -> open trade
  POST /api/close?id=<trade_id>&exit=<price>
  GET  /api/signals -> current whale signals
  GET  /api/whales -> top whales
  GET  /api/journal?uid=<id>
  POST /api/journal -> add note
  GET  /api/price/<symbol>

Static UI served from /static/ (webapp/index.html).
"""
import os
import json
import logging
from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional
from pathlib import Path

from dotenv import load_dotenv
import os
load_dotenv()

from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional
from pathlib import Path

from . import db, whales, market_data

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("gio.web")

app = FastAPI(title="GIO Trading Bot WebApp")
STATIC_DIR = Path(__file__).parent.parent / "webapp"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/me")
async def me(uid: int = Query(...)):
    db.init_db()
    u = db.get_user(uid)
    return {"user_id": u["user_id"], "tier": u["tier"]}


@app.get("/api/price/{symbol}")
async def price(symbol: str):
    return await market_data.price(symbol.upper())


@app.get("/api/signals")
async def signals():
    sigs = await whales.get_signals(min_usd=500000)
    return {"signals": sigs, "sentiment": await whales.fear_greed()}


@app.get("/api/whales")
async def top_whales():
    txs = await whales.etherscan_txlist(addr=os.environ.get("WHALE_WATCH_ADDRESS",
        "0x742d35Cc6634C0532925a3b844Bc9e7595f0bEb0"), limit=10)
    norm = []
    for tx in txs:
        norm.append({
            "hash": tx.get("hash"),
            "from": tx.get("from"),
            "to": tx.get("to"),
            "value": str(int(tx.get("value", "0")) / 1e18),
            "timestamp": int(tx.get("timeStamp", "0")),
        })
    return {"txs": norm}


@app.get("/api/account")
async def binance_account_api():
    from . import binance_client
    from dotenv import load_dotenv
    load_dotenv()
    KEY = os.environ.get("BINANCE_API_KEY", "")
    SEC = os.environ.get("BINANCE_API_SECRET", "")
    if not KEY or not SEC:
        return {"error": "Binance not configured"}
    client = binance_client.BinanceClient(KEY, SEC, testnet=True, is_rsa=False)
    # httpx via wrapper, not async - use sync from async (run in thread)
    import asyncio
    r = await asyncio.to_thread(client.account)
    if "error" in r:
        return r
    return {"balances": [b for b in r.get("balances", []) if float(b.get("free", 0)) > 0]}


@app.get("/api/portfolio")
async def portfolio(uid: int = Query(...)):
    trades = db.list_trades(uid)
    syms = list({t["symbol"] for t in trades if t["status"] == "open"})
    prices = {}
    for s in syms:
        d = await market_data.price(s)
        if "error" not in d:
            cid = next(iter(d.keys()), None)
            if cid and "usd" in d[cid]:
                prices[s] = d[cid]["usd"]
    pnl = db.portfolio_pnl(uid, prices)
    pnl["open_trades"] = [t for t in trades if t["status"] == "open"]
    pnl["closed_trades"] = [t for t in trades if t["status"] == "closed"]
    return pnl


@app.get("/api/watchlist")
async def watchlist_get(uid: int = Query(...)):
    return {"watchlist": db.list_watch(uid)}


@app.post("/api/watch")
async def watch_add(uid: int = Query(...), symbol: str = Query(...)):
    db.init_db()
    db.get_user(uid)
    ok = db.add_watch(uid, symbol)
    return {"ok": ok, "watchlist": db.list_watch(uid)}


@app.delete("/api/watch")
async def watch_del(uid: int = Query(...), symbol: str = Query(...)):
    db.init_db()
    db.get_user(uid)
    ok = db.remove_watch(uid, symbol)
    return {"ok": ok, "watchlist": db.list_watch(uid)}


@app.get("/api/trades")
async def trades_get(uid: int = Query(...)):
    return {"trades": db.list_trades(uid)}


class TradeIn(BaseModel):
    uid: int
    symbol: str
    side: str
    entry_price: float
    size: float
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    notes: Optional[str] = None


@app.post("/api/trade")
async def trade_open(t: TradeIn):
    db.init_db()
    db.get_user(t.uid)
    tid = db.open_trade(t.uid, t.symbol, t.side, t.entry_price, t.size,
                        t.stop_loss, t.take_profit, t.notes)
    return {"id": tid}


@app.post("/api/close")
async def trade_close(uid: int = Query(...), tid: int = Query(...),
                      exit_price: float = Query(...)):
    db.init_db()
    ok = db.close_trade(tid, uid, exit_price)
    return {"ok": ok}


@app.get("/api/journal")
async def journal_get(uid: int = Query(...), symbol: Optional[str] = None):
    return {"journal": db.list_journal(uid, symbol)}


class JournalIn(BaseModel):
    uid: int
    symbol: str
    title: str
    body: str


@app.post("/api/journal")
async def journal_add(j: JournalIn):
    db.init_db()
    db.get_user(j.uid)
    nid = db.add_journal(j.uid, j.symbol, j.title, j.body)
    return {"id": nid}