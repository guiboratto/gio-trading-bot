"""GIO Whale Tracker Bot - canonical webhook entry.

Clean rewrite: no legacy state, no Reply keyboard, only inline.
Single Telegram bot: GIO whale signals + Binance testnet trading.
Run: python -m gio_trading_bot.webhook
"""
import os
import sys
import json
import time
import hmac
import hashlib
import logging
import urllib.request
from datetime import datetime
from urllib.parse import urlencode
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

# ============ config ============

TELEGRAM = "https://api.telegram.org"
TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
BINANCE_KEY = os.environ.get("BINANCE_API_KEY", "")
BINANCE_SEC = os.environ.get("BINANCE_API_SECRET", "")
ETHERSCAN_KEY = os.environ.get("ETHERSCAN_API_KEY", "")
COINGECKO_KEY = os.environ.get("COINGECKO_API_KEY", "")
BINANCE_TESTNET = "https://testnet.binance.vision"
COINGECKO = "https://api.coingecko.com/api/v3"
DEFILLAMA = "https://api.llama.fi"
FEAR = "https://api.alternative.me/fng/?limit=1"
WHALE_WATCH_ADDRESS = "0x742d35Cc6634C0532925a3b844Bc9e7595f0bEb0"
WEBAPP_URL = os.environ.get("WEBAPP_URL", "https://example.com")

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("gio")

# in-memory state (per user)
ONBOARD = {}


# ============ telegram helpers ============

def tg(method, **params):
    url = f"{TELEGRAM}/bot{TOKEN}/{method}"
    headers = {}
    body = None
    if params:
        if method == "sendMessage" and "text" in params:
            # multipart with reply_markup
            boundary = "----gio" + str(int(time.time() * 1000))
            parts = []
            for k, v in params.items():
                parts.append(f"--{boundary}\r\n")
                parts.append(f'Content-Disposition: form-data; name="{k}"\r\n\r\n')
                parts.append(str(v) if not isinstance(v, (dict, list)) else json.dumps(v))
                parts.append("\r\n")
            parts.append(f"--{boundary}--\r\n")
            body = "".join(parts).encode()
            headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        else:
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


# ============ main menu (canonical) ============

def main_menu():
    return kb([
        [{"text": "Live Signals", "callback_data": "m:signals"}],
        [{"text": "Whales", "callback_data": "m:whales"},
         {"text": "Account", "callback_data": "m:account"}],
        [{"text": "Trade", "callback_data": "m:trade"},
         {"text": "Settings", "callback_data": "m:settings"}],
        [{"text": "Open Dashboard", "web_app": {"url": WEBAPP_URL}}],
        [{"text": "Help", "callback_data": "m:help"}],
    ])


def onboarding_kb(step):
    if step == 0:
        return kb([
            [{"text": "English", "callback_data": "ob:lang:en"}],
            [{"text": "Українська", "callback_data": "ob:lang:uk"}],
            [{"text": "Русский", "callback_data": "ob:lang:ru"}],
        ])
    if step == 1:
        return kb([
            [{"text": "New to trading", "callback_data": "ob:profile:new"}],
            [{"text": "I trade already", "callback_data": "ob:profile:trader"}],
            [{"text": "I track whales", "callback_data": "ob:profile:whale"}],
        ])
    if step == 2:
        return kb([
            [{"text": "Open Dashboard", "web_app": {"url": WEBAPP_URL}}],
            [{"text": "Skip - main menu", "callback_data": "ob:finish"}],
        ])
    return None


# ============ data sources ============

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


def binance_account():
    r = binance_signed("GET", "/api/v3/account")
    if "error" in r:
        return r
    return {"balances": [b for b in r.get("balances", [])
                         if float(b.get("free", 0)) > 0]}


def binance_order(symbol, side, qty):
    return binance_signed("POST", "/api/v3/order", {
        "symbol": symbol, "side": side, "type": "MARKET", "quantity": str(qty)
    })


def etherscan_txlist(addr=WHALE_WATCH_ADDRESS, limit=8):
    if not ETHERSCAN_KEY:
        return []
    url = (f"https://api.etherscan.io/v2/api?chainid=1&module=account"
           f"&action=txlist&address={addr}&page=1&offset={limit}"
           f"&sort=desc&apikey={ETHERSCAN_KEY}")
    try:
        with urllib.request.urlopen(url, timeout=8) as r:
            d = json.loads(r.read())
        return d.get("result", []) if d.get("status") == "1" else []
    except Exception as e:
        log.error("etherscan: %s", e)
        return []


def defillama_tvl_movers(threshold=15, n=5):
    try:
        with urllib.request.urlopen(f"{DEFILLAMA}/protocols", timeout=8) as r:
            data = json.loads(r.read())
    except Exception as e:
        log.error("defillama: %s", e)
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


def coingecko_price(sym):
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
            msg = f"{sym} ${p['usd']:,.4f}  24h {p.get('usd_24h_change', 0):+.2f}%"
            if "usd_market_cap" in p:
                msg += f"  mcap ${p['usd_market_cap']:,.0f}"
            return msg
        return f"No data for {sym}"
    except Exception as e:
        return f"err: {e}"


# ============ command handlers ============

def cmd_start(uid, chat_id):
    ONBOARD[uid] = {"step": 0, "lang": "en"}
    send(chat_id,
         "GIO — Whale Tracker\n\n"
         "Live signals, on-chain ETH activity, Binance testnet trading.\n\n"
         "Choose language:",
         reply_markup=onboarding_kb(0))


def cmd_menu(chat_id):
    send(chat_id, "GIO menu:", reply_markup=main_menu())


def cmd_signals(chat_id):
    movers = defillama_tvl_movers()
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
        lines.append("No data. Set COINGECKO_KEY / ETHERSCAN_KEY in .env.")
    lines.append(f"\nFear & Greed: {fear_greed()}")
    send(chat_id, "\n".join(lines))


def cmd_whales(chat_id):
    txs = etherscan_txlist(limit=10)
    if not txs:
        send(chat_id, "No recent ETH tx (or Etherscan key not set).")
        return
    lines = [f"Recent ETH tx (watched: {WHALE_WATCH_ADDRESS[:10]}...):\n"]
    for tx in txs[:6]:
        v = int(tx.get("value", "0")) / 1e18
        ts = int(tx.get("timeStamp", "0"))
        ts_str = datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d %H:%M UTC")
        lines.append(f"  {v:.4f} ETH @ {ts_str}")
        lines.append(f"    {tx['from'][:10]}... -> {tx['to'][:10]}...")
    send(chat_id, "\n".join(lines))


def cmd_account(chat_id):
    r = binance_account()
    if "error" in r:
        send(chat_id, f"err: {r['error']}")
        return
    bs = r["balances"][:10]
    lines = [f"Binance Testnet: {len(r['balances'])} non-zero balances\n"]
    for b in bs:
        lines.append(f"  {b['asset']}: free={b['free']} locked={b['locked']}")
    send(chat_id, "\n".join(lines))


def cmd_buy(chat_id, text):
    return _execute_order(chat_id, text, "BUY")


def cmd_sell(chat_id, text):
    return _execute_order(chat_id, text, "SELL")


def _execute_order(chat_id, text, side):
    parts = text.split()
    if len(parts) < 3:
        send(chat_id, f"Usage: /{side.lower()} SYMBOL QUANTITY\nExample: /{side.lower()} BTCUSDT 0.001")
        return
    sym = parts[1].upper()
    try:
        qty = float(parts[2])
    except ValueError:
        send(chat_id, "Bad quantity.")
        return
    r = binance_order(sym, side, qty)
    if "error" in r:
        send(chat_id, f"Order err: {r['error']}\n{r.get('body', '')[:200]}")
        return
    send(chat_id, f"{side} {qty} {sym} {r.get('status')}\n"
                    f"orderId={r.get('orderId')}\n"
                    f"qty={r.get('executedQty')} quote={r.get('cummulativeQuoteQty')}")


def cmd_price(chat_id, text):
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        send(chat_id, "Usage: /price BTC")
        return
    send(chat_id, coingecko_price(parts[1].strip().upper()))


def cmd_settings(chat_id):
    has_binance = bool(BINANCE_KEY)
    has_eth = bool(ETHERSCAN_KEY)
    has_cg = bool(COINGECKO_KEY)
    send(chat_id,
         f"Settings (server-side):\n"
         f"  Binance Testnet: {'on' if has_binance else 'off'}\n"
         f"  Etherscan V2: {'on' if has_eth else 'off'}\n"
         f"  CoinGecko: {'on' if has_cg else 'off'}\n\n"
         f"Commands: /signals, /whales, /account, /buy, /sell, /price SYM",
         reply_markup=kb([[{"text": "Back", "callback_data": "m:main"}]]))


def cmd_help(chat_id):
    send(chat_id,
         "GIO commands:\n"
         "/start - onboarding\n"
         "/menu - main menu (inline buttons)\n"
         "/signals - live signals (DefiLlama + Etherscan)\n"
         "/whales - recent ETH tx\n"
         "/account - Binance Testnet balances\n"
         "/buy SYM QTY - market buy\n"
         "/sell SYM QTY - market sell\n"
         "/price SYM - live price (CoinGecko)\n"
         "/settings - status\n"
         "/help - this help")


# ============ callback router ============

def on_callback(uid, chat_id, msg_id, cb_id, data):
    answer(cb_id)
    log.info("CB user=%s data=%s", uid, data)

    if data == "m:main":
        edit("GIO menu:", chat_id, msg_id, reply_markup=main_menu())
    elif data == "m:signals":
        edit(_signals_text(), chat_id, msg_id, reply_markup=kb([[{"text": "Back", "callback_data": "m:main"}]]))
    elif data == "m:whales":
        edit(_whales_text(), chat_id, msg_id, reply_markup=kb([[{"text": "Back", "callback_data": "m:main"}]]))
    elif data == "m:account":
        edit(_account_text(), chat_id, msg_id, reply_markup=kb([[{"text": "Back", "callback_data": "m:main"}]]))
    elif data == "m:trade":
        edit("Trade on Binance Testnet:\n/buy SYM QTY\n/sell SYM QTY",
             chat_id, msg_id, reply_markup=kb([[{"text": "Back", "callback_data": "m:main"}]]))
    elif data == "m:settings":
        has_b = bool(BINANCE_KEY); has_e = bool(ETHERSCAN_KEY); has_c = bool(COINGECKO_KEY)
        edit(f"Settings:\n  Binance: {'on' if has_b else 'off'}\n"
             f"  Etherscan: {'on' if has_e else 'off'}\n"
             f"  CoinGecko: {'on' if has_c else 'off'}",
             chat_id, msg_id, reply_markup=kb([[{"text": "Back", "callback_data": "m:main"}]]))
    elif data == "m:help":
        edit(cmd_help.__doc__ or "GIO commands: /signals /whales /account /buy /sell /price /settings",
             chat_id, msg_id, reply_markup=kb([[{"text": "Back", "callback_data": "m:main"}]]))
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


def _signals_text():
    movers = defillama_tvl_movers()
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
        lines.append("No data. Set API keys in .env.")
    lines.append(f"\nFear & Greed: {fear_greed()}")
    return "\n".join(lines)


def _whales_text():
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


def _account_text():
    r = binance_account()
    if "error" in r:
        return f"err: {r['error']}"
    bs = r["balances"][:10]
    lines = [f"Binance Testnet: {len(r['balances'])} non-zero balances\n"]
    for b in bs:
        lines.append(f"  {b['asset']}: free={b['free']} locked={b['locked']}")
    return "\n".join(lines)


# ============ HTTP ============

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a, **kw): pass

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"GIO whale tracker webhook")

    def do_POST(self):
        n = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(n).decode("utf-8", "ignore")
        log.info("UPDATE: %s", body[:200])
        try:
            d = json.loads(body)
        except Exception:
            self.send_response(200); self.end_headers(); self.wfile.write(b"OK")
            return
        if "message" in d:
            self._on_message(d["message"])
        elif "callback_query" in d:
            self._on_callback(d["callback_query"])
        self.send_response(200); self.end_headers(); self.wfile.write(b"OK")

    def _on_message(self, m):
        uid = m["from"]["id"]
        chat_id = m["chat"]["id"]
        text = m.get("text", "")
        log.info("MSG user=%s text=%r", uid, text[:80])
        if text.startswith("/start"):
            cmd_start(uid, chat_id)
        elif text.startswith("/menu"):
            cmd_menu(chat_id)
        elif text.startswith("/signals"):
            cmd_signals(chat_id)
        elif text.startswith("/whales"):
            cmd_whales(chat_id)
        elif text.startswith("/account"):
            cmd_account(chat_id)
        elif text.startswith("/buy "):
            cmd_buy(chat_id, text)
        elif text.startswith("/sell "):
            cmd_sell(chat_id, text)
        elif text.startswith("/price"):
            cmd_price(chat_id, text)
        elif text.startswith("/settings"):
            cmd_settings(chat_id)
        elif text.startswith("/help"):
            cmd_help(chat_id)
        elif not text.startswith("/"):
            send(chat_id, f"Note saved: {text[:100]}")

    def _on_callback(self, c):
        uid = c["from"]["id"]
        chat_id = c["message"]["chat"]["id"]
        msg_id = c["message"]["message_id"]
        cb_id = c["id"]
        data = c.get("data", "")
        on_callback(uid, chat_id, msg_id, cb_id, data)


class ReuseHTTPServer(HTTPServer):
    allow_reuse_address = True


def main():
    log.info("GIO webhook starting on 0.0.0.0:8765")
    log.info("Binance=%s Etherscan=%s CoinGecko=%s",
             "yes" if BINANCE_KEY else "no",
             "yes" if ETHERSCAN_KEY else "no",
             "yes" if COINGECKO_KEY else "no")
    log.info("WEBAPP_URL=%s", WEBAPP_URL)
    ReuseHTTPServer(("0.0.0.0", 8765), Handler).serve_forever()


if __name__ == "__main__":
    main()