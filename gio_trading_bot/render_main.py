"""Render entrypoint: webhook bot + FastAPI miniapp on one process.
Webhook: POST /telegram. MiniApp: GET /.
"""
import os
import json
import time
import hmac
import hashlib
import logging
import urllib.request
from datetime import datetime
from urllib.parse import urlencode
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"), override=True)

TELEGRAM = "https://api.telegram.org"
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
BINANCE_KEY = os.environ.get("BINANCE_API_KEY", "")
BINANCE_SEC = os.environ.get("BINANCE_API_SECRET", "")
ETHERSCAN_KEY = os.environ.get("ETHERSCAN_API_KEY", "")
COINGECKO_KEY = os.environ.get("COINGECKO_API_KEY", "")
BINANCE_TESTNET = "https://testnet.binance.vision"
COINGECKO = "https://api.coingecko.com/api/v3"
DEFILLAMA = "https://api.llama.fi"
FEAR = "https://api.alternative.me/fng/?limit=1"
WHALE_WATCH_ADDRESS = "0x742d35Cc6634C0532925a3b844Bc9e7595f0bEb0"

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("gio")

ONBOARD = {}


def tg(method, **params):
    url = f"{TELEGRAM}/bot{TOKEN}/{method}"
    headers = {}
    body = None
    if params:
        body = json.dumps(params).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except Exception as e:
        log.error("tg %s: %s", method, e)
        return {}


def send(chat_id, text, reply_markup=None):
    p = {"chat_id": chat_id, "text": text[:4000]}
    if reply_markup:
        p["reply_markup"] = json.dumps(reply_markup)
    return tg("sendMessage", **p)


def edit(text, chat_id, message_id, reply_markup=None):
    p = {"chat_id": chat_id, "message_id": message_id, "text": text[:4000]}
    if reply_markup:
        p["reply_markup"] = json.dumps(reply_markup)
    return tg("editMessageText", **p)


def answer(cb_id, text=""):
    return tg("answerCallbackQuery", callback_query_id=cb_id, text=text)


def kb(rows):
    return {"inline_keyboard": rows}


def binance_signed(method, path, params=None):
    if not BINANCE_KEY or not BINANCE_SEC:
        return {"error": "Binance not configured"}
    params = dict(params or {})
    params["timestamp"] = int(time.time() * 1000)
    params.setdefault("recvWindow", 5000)
    qs = urlencode(params)
    sig = hmac.new(BINANCE_SEC.encode(), qs.encode(), hashlib.sha256).hexdigest()
    url = f"{BINANCE_TESTNET}{path}?{qs}&signature={sig}"
    req = urllib.request.Request(url, headers={"X-MBX-APIKEY": BINANCE_KEY}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}", "body": e.read().decode()[:200]}
    except Exception as e:
        return {"error": str(e)}


def etherscan_txlist(limit=10):
    if not ETHERSCAN_KEY:
        return []
    url = (f"https://api.etherscan.io/v2/api?chainid=1&module=account"
           f"&action=txlist&address={WHALE_WATCH_ADDRESS}&page=1&offset={limit}"
           f"&sort=desc&apikey={ETHERSCAN_KEY}")
    try:
        with urllib.request.urlopen(url, timeout=8) as r:
            d = json.loads(r.read())
        return d.get("result", []) if d.get("status") == "1" else []
    except Exception as e:
        log.error("etherscan: %s", e)
        return []


def defillama_movers(threshold=15, n=5):
    try:
        with urllib.request.urlopen(f"{DEFILLAMA}/protocols", timeout=8) as r:
            data = json.loads(r.read())
    except Exception as e:
        return []
    movers = []
    for p in data:
        c = p.get("change_1d")
        if c is not None and abs(c) >= threshold:
            movers.append(p)
    movers.sort(key=lambda p: abs(p.get("change_1d", 0)), reverse=True)
    return movers[:n]


def fear_greed():
    try:
        with urllib.request.urlopen(FEAR, timeout=8) as r:
            d = json.loads(r.read())["data"][0]
        return f"{d['value']} ({d['value_classification']})"
    except Exception:
        return "n/a"


def coin_price(sym):
    m = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana",
         "BNB": "binancecoin", "XRP": "ripple", "USDT": "tether"}
    cid = m.get(sym.upper(), sym.lower())
    url = (f"{COINGECKO}/simple/price?ids={cid}&vs_currencies=usd"
           f"&include_24hr_change=true&market_cap=true")
    if COINGECKO_KEY:
        url += f"&x_cg_demo_api_key={COINGECKO_KEY}"
    try:
        with urllib.request.urlopen(url, timeout=8) as r:
            d = json.loads(r.read())
        if cid in d:
            p = d[cid]
            msg = f"{sym} ${p['usd']:,.4f} 24h {p.get('usd_24h_change', 0):+.2f}%"
            if "usd_market_cap" in p:
                msg += f" mcap ${p['usd_market_cap']:,.0f}"
            return msg
        return f"No data for {sym}"
    except Exception as e:
        return f"err: {e}"


def main_menu():
    webapp_url = "https://" + os.environ.get("RENDER_EXTERNAL_URL", "example.com")
    return kb([
        [{"text": "Live Signals", "callback_data": "m:signals"}],
        [{"text": "Whales", "callback_data": "m:whales"},
         {"text": "Account", "callback_data": "m:account"}],
        [{"text": "Trade", "callback_data": "m:trade"},
         {"text": "Settings", "callback_data": "m:settings"}],
        [{"text": "Open Dashboard", "web_app": {"url": webapp_url}}],
        [{"text": "Help", "callback_data": "m:help"}],
    ])


def onboarding_kb(step):
    if step == 0:
        return kb([
            [{"text": "English", "callback_data": "ob:lang:en"}],
            [{"text": "Ukrainian", "callback_data": "ob:lang:uk"}],
            [{"text": "Russian", "callback_data": "ob:lang:ru"}],
        ])
    if step == 1:
        return kb([
            [{"text": "New to trading", "callback_data": "ob:profile:new"}],
            [{"text": "I trade already", "callback_data": "ob:profile:trader"}],
            [{"text": "I track whales", "callback_data": "ob:profile:whale"}],
        ])
    if step == 2:
        webapp_url = "https://" + os.environ.get("RENDER_EXTERNAL_URL", "example.com")
        return kb([
            [{"text": "Open Dashboard", "web_app": {"url": webapp_url}}],
            [{"text": "Skip - main menu", "callback_data": "ob:finish"}],
        ])


def signals_text():
    movers = defillama_movers()
    txs = etherscan_txlist()
    lines = ["Live signals:\n"]
    if movers:
        lines.append("DeFiLlama TVL movers:")
        for m in movers:
            lines.append(f"  - {m.get('name')}: ${m.get('tvl', 0):,.0f} ({m['change_1d']:+.1f}%)")
    if txs:
        lines.append("\nEtherscan ETH flow:")
        for tx in txs[:3]:
            v = int(tx.get("value", "0")) / 1e18
            ts = int(tx.get("timeStamp", "0"))
            ts_str = datetime.utcfromtimestamp(ts).strftime("%m-%d %H:%M UTC")
            lines.append(f"  - {v:.4f} ETH @ {ts_str}")
    if not movers and not txs:
        lines.append("No data. Set API keys in env.")
    lines.append(f"\nFear & Greed: {fear_greed()}")
    return "\n".join(lines)


def whales_text():
    txs = etherscan_txlist(limit=10)
    if not txs:
        return "No recent ETH tx."
    lines = [f"Recent ETH tx (watched {WHALE_WATCH_ADDRESS[:10]}...):\n"]
    for tx in txs[:6]:
        v = int(tx.get("value", "0")) / 1e18
        ts = int(tx.get("timeStamp", "0"))
        ts_str = datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d %H:%M UTC")
        lines.append(f"  {v:.4f} ETH @ {ts_str}")
        lines.append(f"    {tx['from'][:10]}... -> {tx['to'][:10]}...")
    return "\n".join(lines)


def account_text():
    r = binance_signed("GET", "/api/v3/account")
    if "error" in r:
        return f"err: {r['error']}"
    bs = [b for b in r.get("balances", []) if float(b.get("free", 0)) > 0]
    lines = [f"Binance Testnet: {len(bs)} non-zero balances\n"]
    for b in bs[:10]:
        lines.append(f"  {b['asset']}: free={b['free']} locked={b['locked']}")
    return "\n".join(lines)


def settings_text():
    has_b = bool(BINANCE_KEY); has_e = bool(ETHERSCAN_KEY); has_c = bool(COINGECKO_KEY)
    return (f"Settings:\n  Binance: {'on' if has_b else 'off'}\n"
            f"  Etherscan: {'on' if has_e else 'off'}\n"
            f"  CoinGecko: {'on' if has_c else 'off'}")


def help_text():
    return ("GIO commands:\n/start - onboarding\n/menu - main menu\n"
            "/signals - live signals\n/whales - recent ETH tx\n"
            "/account - Binance Testnet balances\n/buy SYM QTY\n/sell SYM QTY\n"
            "/price SYM - live price\n/settings - status\n/help")


def cmd_start(uid, chat_id):
    ONBOARD[uid] = {"step": 0, "lang": "en"}
    send(chat_id, "GIO - Whale Tracker\n\nLive signals, on-chain ETH activity, Binance testnet trading.\n\nChoose language:",
         reply_markup=onboarding_kb(0))


def cmd_menu(chat_id):
    send(chat_id, "GIO menu:", reply_markup=main_menu())


def cmd_signals(chat_id):
    send(chat_id, signals_text())


def cmd_whales(chat_id):
    send(chat_id, whales_text())


def cmd_account(chat_id):
    send(chat_id, account_text())


def cmd_execute_order(chat_id, text, side):
    parts = text.split()
    if len(parts) < 3:
        send(chat_id, f"Usage: /{side.lower()} SYMBOL QUANTITY")
        return
    sym = parts[1].upper()
    try:
        qty = float(parts[2])
    except ValueError:
        send(chat_id, "Bad quantity.")
        return
    r = binance_signed("POST", "/api/v3/order", {
        "symbol": sym, "side": side, "type": "MARKET", "quantity": str(qty)
    })
    if "error" in r:
        send(chat_id, f"Order err: {r['error']}\n{r.get('body', '')[:200]}")
        return
    send(chat_id, f"{side} {qty} {sym} {r.get('status')}\norderId={r.get('orderId')}\nqty={r.get('executedQty')} quote={r.get('cummulativeQuoteQty')}")


def cmd_settings(chat_id):
    send(chat_id, settings_text(), reply_markup=kb([[{"text": "Back", "callback_data": "m:main"}]]))


def cmd_help(chat_id):
    send(chat_id, help_text())


def cmd_price(chat_id, text):
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        send(chat_id, "Usage: /price BTC")
        return
    send(chat_id, coin_price(parts[1].strip().upper()))


def on_callback(uid, chat_id, msg_id, cb_id, data):
    answer(cb_id)
    log.info("CB user=%s data=%s", uid, data)
    if data == "m:main":
        edit("GIO menu:", chat_id, msg_id, reply_markup=main_menu())
    elif data == "m:signals":
        edit(signals_text(), chat_id, msg_id, reply_markup=kb([[{"text": "Back", "callback_data": "m:main"}]]))
    elif data == "m:whales":
        edit(whales_text(), chat_id, msg_id, reply_markup=kb([[{"text": "Back", "callback_data": "m:main"}]]))
    elif data == "m:account":
        edit(account_text(), chat_id, msg_id, reply_markup=kb([[{"text": "Back", "callback_data": "m:main"}]]))
    elif data == "m:trade":
        edit("Trade on Binance Testnet:\n/buy SYM QTY\n/sell SYM QTY",
             chat_id, msg_id, reply_markup=kb([[{"text": "Back", "callback_data": "m:main"}]]))
    elif data == "m:settings":
        edit(settings_text(), chat_id, msg_id, reply_markup=kb([[{"text": "Back", "callback_data": "m:main"}]]))
    elif data == "m:help":
        edit(help_text(), chat_id, msg_id, reply_markup=kb([[{"text": "Back", "callback_data": "m:main"}]]))
    elif data.startswith("ob:"):
        parts = data.split(":")
        if parts[1] == "lang":
            ONBOARD.setdefault(uid, {})["lang"] = parts[2]
            ONBOARD[uid]["step"] = 1
            edit("Choose profile:", chat_id, msg_id, reply_markup=onboarding_kb(1))
        elif parts[1] == "profile":
            ONBOARD[uid]["profile"] = parts[2]
            ONBOARD[uid]["step"] = 2
            edit("Finish onboarding:", chat_id, msg_id, reply_markup=onboarding_kb(2))
        elif parts[1] == "finish":
            ONBOARD.pop(uid, None)
            edit("GIO menu:", chat_id, msg_id, reply_markup=main_menu())


from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path

app = FastAPI(title="GIO Whale Tracker")
STATIC_DIR = Path(__file__).parent.parent / "webapp"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/signals")
async def api_signals():
    movers = defillama_movers()
    txs = etherscan_txlist(limit=10)
    norm = []
    for m in movers:
        norm.append({
            "source": "defillama", "asset": m.get("name"),
            "amount_usd": m.get("tvl", 0), "direction": "tvl_up" if m["change_1d"] > 0 else "tvl_down",
            "change_pct": m["change_1d"], "timestamp": int(time.time())
        })
    for tx in txs[:5]:
        norm.append({
            "source": "etherscan", "asset": "ETH",
            "amount_usd": int(tx.get("value", "0")) / 1e18 * 3000,
            "direction": "transfer", "timestamp": int(tx.get("timeStamp", "0")),
        })
    norm.sort(key=lambda s: s.get("amount_usd", 0), reverse=True)
    fg_val, fg_class = None, None
    try:
        with urllib.request.urlopen(FEAR, timeout=5) as r:
            d = json.loads(r.read())["data"][0]
        fg_val, fg_class = d["value"], d["value_classification"]
    except Exception:
        pass
    return {"signals": norm, "sentiment": {"value": fg_val, "classification": fg_class}}


@app.get("/api/whales")
async def api_whales():
    txs = etherscan_txlist(limit=10)
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
async def api_account():
    if not BINANCE_KEY or not BINANCE_SEC:
        return {"error": "Binance not configured"}
    r = binance_signed("GET", "/api/v3/account")
    if "error" in r:
        return r
    return {"error": "Binance API blocked on Render datacenter. Run /home/guiboratto/projects/gio-trading-bot/webhook.py locally for trading."}


@app.get("/api/price/{symbol}")
async def api_price(symbol: str):
    return {"price": coin_price(symbol.upper())}


@app.post("/api/trade")
async def api_trade(side: str, symbol: str, qty: float):
    """Testnet market order. Returns Binance error (HTTP 451) on Render datacenter."""
    if not BINANCE_KEY or not BINANCE_SEC:
        return {"error": "Binance keys not set"}
    r = binance_signed("POST", "/api/v3/order", {
        "symbol": symbol.upper(), "side": side.upper(), "type": "MARKET",
        "quantity": str(qty)
    })
    if "error" in r:
        return r
    return r


EVENT_LOG = []  # ring buffer, last 200 events

@app.get("/api/debug/log")
async def api_debug_log(limit: int = 50):
    return {"events": EVENT_LOG[-limit:]}


@app.get("/api/market-overview")
async def api_market_overview():
    """Multi-source overview: Coingecko top movers + DefiLlama + sentiment."""
    overview = {"coingecko_top_movers": [], "defillama_movers": [], "sentiment": None}
    # Coingecko top movers
    try:
        url = f"{COINGECKO}/coins/markets?vs_currency=usd&order=percent_change_24h_desc&per_page=10&page=1&sparkline=false&price_change_percentage=24h"
        if COINGECKO_KEY:
            url += f"&x_cg_demo_api_key={COINGECKO_KEY}"
        req = urllib.request.Request(url, headers={"User-Agent": "gio-bot/1.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read())
        overview["coingecko_top_movers"] = [{
            "symbol": c.get("symbol", "").upper(),
            "name": c.get("name", ""),
            "price": c.get("current_price"),
            "change_24h_pct": c.get("price_change_percentage_24h"),
            "market_cap": c.get("market_cap"),
            "volume_24h": c.get("total_volume"),
        } for c in data[:10]]
    except Exception as e:
        log.error("coingecko movers: %s", e)
    overview["defillama_movers"] = [{
        "name": m.get("name"),
        "category": m.get("category"),
        "tvl_usd": m.get("tvl"),
        "change_1d_pct": m.get("change_1d"),
    } for m in defillama_movers(n=5)]
    overview["sentiment"] = fear_greed()
    return overview


@app.get("/telegram")
async def telegram_webhook_root():
    return {"status": "ok", "msg": "send POST"}


@app.post("/telegram")
async def telegram_webhook(request: Request):
    try:
        d = await request.json()
    except Exception:
        return {"ok": True}
    log.info("UPDATE: %s", str(d)[:200])
    if "message" in d:
        m = d["message"]
        uid = m["from"]["id"]
        chat_id = m["chat"]["id"]
        text = m.get("text", "")
        log.info("MSG user=%s text=%r", uid, text[:80])
        EVENT_LOG.append({"ts": int(time.time()), "type": "msg", "uid": uid, "text": text[:80]})
        EVENT_LOG[:] = EVENT_LOG[-200:]
        if text.startswith("/start"): cmd_start(uid, chat_id)
        elif text.startswith("/menu"): cmd_menu(chat_id)
        elif text.startswith("/signals"): cmd_signals(chat_id)
        elif text.startswith("/whales"): cmd_whales(chat_id)
        elif text.startswith("/account"): cmd_account(chat_id)
        elif text.startswith("/buy "): cmd_execute_order(chat_id, text, "BUY")
        elif text.startswith("/sell "): cmd_execute_order(chat_id, text, "SELL")
        elif text.startswith("/settings"): cmd_settings(chat_id)
        elif text.startswith("/help"): cmd_help(chat_id)
        elif text.startswith("/price"): cmd_price(chat_id, text)
        elif not text.startswith("/"):
            send(chat_id, f"Note saved: {text[:100]}")
    elif "callback_query" in d:
        c = d["callback_query"]
        EVENT_LOG.append({"ts": int(time.time()), "type": "cb", "data": c.get("data", "")})
        EVENT_LOG[:] = EVENT_LOG[-200:]
        on_callback(c["from"]["id"], c["message"]["chat"]["id"],
                    c["message"]["message_id"], c["id"], c.get("data", ""))
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", "8000"))
    log.info("Starting GIO on port %d", port)
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
