"""GIO Trading Bot — clean Telegram interface.

No conversation states. Single-command interface + Mini App button.
Persistent watchlist, trades, journal via db.py.
"""
import os
import json
import asyncio
import logging
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters,
)
from . import db, whales, archam, market_data, advisor as adv

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("gio")

FREE_DAILY_ANALYZE = 3


def main():
    db.init_db()
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    app = ApplicationBuilder().token(token).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("price", cmd_price))
    app.add_handler(CommandHandler("watch", cmd_watch))
    app.add_handler(CommandHandler("unwatch", cmd_unwatch))
    app.add_handler(CommandHandler("watchlist", cmd_watchlist))
    app.add_handler(CommandHandler("buy", cmd_buy))
    app.add_handler(CommandHandler("sell", cmd_sell))
    app.add_handler(CommandHandler("close", cmd_close))
    app.add_handler(CommandHandler("trades", cmd_trades))
    app.add_handler(CommandHandler("portfolio", cmd_portfolio))
    app.add_handler(CommandHandler("note", cmd_note))
    app.add_handler(CommandHandler("notes", cmd_notes))
    app.add_handler(CommandHandler("risk", cmd_risk))
    app.add_handler(CommandHandler("whales", cmd_whales))
    app.add_handler(CommandHandler("signals", cmd_signals))
    app.add_handler(CommandHandler("analyze", cmd_analyze))
    app.add_handler(CommandHandler("tier", cmd_tier))
    app.add_handler(CallbackQueryHandler(tier_picked, pattern="^tier:"))
    # catch-all: anything else = free-form note to advisor (with limit)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, free_text))

    # background
    app.job_queue.run_once(_kick_monitor, when=5)
    log.info("Bot starting...")
    app.run_polling(drop_pending_updates=True)


# ============ monitor ============

async def _kick_monitor(ctx):
    asyncio.create_task(_run_monitor(ctx))


async def _run_monitor(ctx):
    """Discover whales every 5 min. Push alerts when tokens on watchlist get bought."""
    from telegram import Bot
    bot = Bot(os.environ["TELEGRAM_BOT_TOKEN"])
    last_seen: set = set()
    while True:
        try:
            ws = await whales.discover_whales(limit=30)
            if ws:
                sigs = await whales.find_signals(ws, min_buyers=2)
                # notify new signals to anyone watching any of these tokens
                fresh = []
                for s in sigs:
                    key = f"{s['token']}:{s['n_buyers']}"
                    if key not in last_seen:
                        last_seen.add(key)
                        fresh.append(s)
                for s in fresh:
                    targets = db.all_watchers_for(s["token"], s.get("chain", "eth"))
                    text = (
                        f"WHALE BUY ALERT\n\n"
                        f"Token: {s['token']}\n"
                        f"Buyers: {s['n_buyers']} whales\n"
                        f"Combined PnL: ${s['total_pnl_usd']:,.0f}\n"
                        f"Detected: {datetime.utcnow().strftime('%H:%M UTC')}"
                    )
                    for uid in targets:
                        try:
                            await bot.send_message(chat_id=uid, text=text)
                        except Exception as e:
                            log.warning("alert send fail %s: %s", uid, e)
                log.info("monitor: whales=%d signals=%d fresh=%d",
                         len(ws), len(sigs), len(fresh))
        except Exception as e:
            log.exception("monitor: %s", e)
        await asyncio.sleep(300)


# ============ handlers ============

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.get_user(user.id)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Open Mini App",
                              web_app=WebAppInfo(url=os.environ.get(
                                  "WEBAPP_URL",
                                  "https://example.com")))],
        [InlineKeyboardButton("Quick commands", callback_data="tier:help")],
    ])
    await update.message.reply_text(
        f"Hi {user.first_name}! GIO Trading Bot v3.\n\n"
        f"Real functions:\n"
        f"  /price BTC - live price\n"
        f"  /watch BTC - add to watchlist (alerts on whale buys)\n"
        f"  /watchlist - show your tokens\n"
        f"  /buy BTC 65000 0.5 - open long trade\n"
        f"  /sell BTC 64500 0.5 - open short trade\n"
        f"  /close 12 - close trade #12\n"
        f"  /trades - open positions\n"
        f"  /portfolio - PnL summary\n"
        f"  /note BTC 'title' 'body' - journal\n"
        f"  /risk 500 63000 69000 - position size calc\n"
        f"  /whales - top tracked whales\n"
        f"  /signals - current whale signals\n"
        f"  /analyze BTC - AI deep dive\n"
        f"  /tier - subscription",
        reply_markup=kb,
    )


async def cmd_help(update, ctx):
    await update.message.reply_text(
        "Commands:\n"
        "/price <SYM> - live price (CoinGecko)\n"
        "/watch <SYM> - add to watchlist\n"
        "/unwatch <SYM> - remove\n"
        "/watchlist - list\n"
        "/buy <SYM> <price> <size> [stop] [target] - open long\n"
        "/sell <SYM> <price> <size> [stop] [target] - open short\n"
        "/close <id> <exit_price> - close trade\n"
        "/trades [open|closed] - list positions\n"
        "/portfolio - PnL summary\n"
        "/note <SYM> <title> | <body> - add journal\n"
        "/notes [SYM] - show journal\n"
        "/risk <size_usd> <stop> <target> - position calc\n"
        "/whales - top whales\n"
        "/signals - whale buy signals\n"
        "/analyze <SYM> - AI analysis (limited)\n"
        "/tier - subscription"
    )


async def cmd_price(update, ctx):
    parts = (update.message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await update.message.reply_text("Usage: /price BTC")
        return
    sym = parts[1].strip().upper()
    data = await market_data.price(sym)
    if "error" in data:
        await update.message.reply_text(f"Error: {data['error']}")
        return
    cid = next(iter(data.keys()), None)
    if not cid:
        await update.message.reply_text(f"No data for {sym}")
        return
    p = data[cid].get("usd")
    ch = data[cid].get("usd_24h_change")
    mc = data[cid].get("usd_market_cap")
    msg = f"{sym} ${p:,.4f}" if p else f"{sym}: no price"
    if ch is not None:
        msg += f"  24h {ch:+.2f}%"
    if mc:
        msg += f"  mcap ${mc:,.0f}"
    await update.message.reply_text(msg)


async def cmd_watch(update, ctx):
    parts = (update.message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await update.message.reply_text("Usage: /watch BTC")
        return
    sym = parts[1].strip().upper()
    ok = db.add_watch(update.effective_user.id, sym)
    await update.message.reply_text(
        f"Tracking {sym}. You'll get alerts when whales buy." if ok
        else f"{sym} already in watchlist."
    )


async def cmd_unwatch(update, ctx):
    parts = (update.message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await update.message.reply_text("Usage: /unwatch BTC")
        return
    sym = parts[1].strip().upper()
    ok = db.remove_watch(update.effective_user.id, sym)
    await update.message.reply_text(f"Removed {sym}" if ok else f"{sym} not in watchlist")


async def cmd_watchlist(update, ctx):
    items = db.list_watch(update.effective_user.id)
    if not items:
        await update.message.reply_text("Watchlist empty. /watch BTC to start.")
        return
    lines = [f"Your watchlist ({len(items)}):"]
    for i in items:
        lines.append(f"  {i['symbol']} ({i['chain']})")
    await update.message.reply_text("\n".join(lines))


# ============ trading ============

async def _parse_trade_args(text: str, side: str):
    """Parse: /buy BTC 65000 0.5 [stop] [target] [notes...]"""
    parts = text.split()
    if len(parts) < 4:
        return None
    try:
        symbol = parts[1].upper()
        entry = float(parts[2])
        size = float(parts[3])
        stop = float(parts[4]) if len(parts) > 4 else None
        target = float(parts[5]) if len(parts) > 5 else None
        notes = " ".join(parts[6:]) if len(parts) > 6 else None
        return {"symbol": symbol, "entry": entry, "size": size,
                "stop": stop, "target": target, "notes": notes}
    except (ValueError, IndexError):
        return None


async def cmd_buy(update, ctx):
    args = await _parse_trade_args(update.message.text, "buy")
    if not args:
        await update.message.reply_text(
            "Usage: /buy SYMBOL ENTRY_PRICE SIZE [stop] [target] [notes]\n"
            "Example: /buy BTC 65000 0.5 63000 70000 'long breakout'"
        )
        return
    tid = db.open_trade(update.effective_user.id, args["symbol"], "buy",
                        args["entry"], args["size"], args["stop"], args["target"],
                        args["notes"])
    sl = f" SL={args['stop']}" if args["stop"] else ""
    tp = f" TP={args['target']}" if args["target"] else ""
    await update.message.reply_text(
        f"Trade #{tid} opened: BUY {args['symbol']} @ {args['entry']} "
        f"size={args['size']}{sl}{tp}\n/close {tid} <exit_price>")


async def cmd_sell(update, ctx):
    args = await _parse_trade_args(update.message.text, "sell")
    if not args:
        await update.message.reply_text(
            "Usage: /sell SYMBOL ENTRY_PRICE SIZE [stop] [target]"
        )
        return
    tid = db.open_trade(update.effective_user.id, args["symbol"], "sell",
                        args["entry"], args["size"], args["stop"], args["target"],
                        args["notes"])
    await update.message.reply_text(
        f"Trade #{tid} opened: SELL {args['symbol']} @ {args['entry']} size={args['size']}"
    )


async def cmd_close(update, ctx):
    parts = (update.message.text or "").split()
    if len(parts) < 3:
        await update.message.reply_text("Usage: /close <trade_id> <exit_price>")
        return
    try:
        tid = int(parts[1])
        exit_price = float(parts[2])
    except ValueError:
        await update.message.reply_text("Bad trade_id or exit_price")
        return
    ok = db.close_trade(tid, update.effective_user.id, exit_price)
    await update.message.reply_text(
        f"Trade #{tid} closed @ {exit_price}" if ok else f"Trade #{tid} not found or already closed"
    )


async def cmd_trades(update, ctx):
    parts = (update.message.text or "").split(maxsplit=1)
    status = parts[1].strip() if len(parts) > 1 and parts[1] in ("open", "closed") else None
    trades = db.list_trades(update.effective_user.id, status)
    if not trades:
        await update.message.reply_text("No trades. /buy SYM PRICE SIZE to open.")
        return
    lines = []
    for t in trades[:20]:
        icon = "+" if t["side"] == "buy" else "-"
        flag = "OPEN" if t["status"] == "open" else "CLOSED"
        lines.append(
            f"#{t['id']} [{flag}] {icon}{t['symbol']} @ {t['entry_price']} "
            f"size={t['size']}"
            + (f" → {t['exit_price']}" if t['exit_price'] else "")
        )
    await update.message.reply_text("\n".join(lines))


async def cmd_portfolio(update, ctx):
    user_id = update.effective_user.id
    trades = db.list_trades(user_id)
    if not trades:
        await update.message.reply_text("No trades.")
        return
    # fetch current prices for unique symbols
    syms = list({t["symbol"] for t in trades if t["status"] == "open"})
    prices = {}
    for s in syms:
        d = await market_data.price(s)
        if "error" not in d:
            cid = next(iter(d.keys()), None)
            if cid and "usd" in d[cid]:
                prices[s] = d[cid]["usd"]
    pnl = db.portfolio_pnl(user_id, prices)
    msg = (
        f"Portfolio\n"
        f"Open: {pnl['n_open']}  Closed: {pnl['n_closed']}\n"
        f"Realized PnL: ${pnl['realized']:,.2f}\n"
        f"Unrealized PnL: ${pnl['unrealized']:,.2f}\n"
        f"Total: ${pnl['realized'] + pnl['unrealized']:,.2f}"
    )
    await update.message.reply_text(msg)


# ============ journal ============

async def cmd_note(update, ctx):
    """Usage: /note SYMBOL title | body"""
    text = update.message.text or ""
    parts = text.split(maxsplit=2)
    if len(parts) < 3 or "|" not in parts[2]:
        await update.message.reply_text(
            "Usage: /note SYMBOL title | body\n"
            "Example: /note BTC breakout retest | waiting for 65k support"
        )
        return
    symbol = parts[1].upper()
    title_body = parts[2].split("|", 1)
    title = title_body[0].strip()
    body = title_body[1].strip() if len(title_body) > 1 else ""
    nid = db.add_journal(update.effective_user.id, symbol, title, body)
    await update.message.reply_text(f"Note #{nid} saved for {symbol}: {title}")


async def cmd_notes(update, ctx):
    parts = (update.message.text or "").split(maxsplit=1)
    sym = parts[1].upper() if len(parts) > 1 else None
    notes = db.list_journal(update.effective_user.id, sym)
    if not notes:
        await update.message.reply_text("No notes.")
        return
    lines = []
    for n in notes[:15]:
        ts = datetime.fromtimestamp(n["created_at"]).strftime("%Y-%m-%d")
        lines.append(f"[{ts}] {n['symbol']}: {n['title']}\n  {n['body'][:200]}")
    await update.message.reply_text("\n\n".join(lines))


# ============ risk calculator ============

async def cmd_risk(update, ctx):
    """Usage: /risk <account_size_usd> <stop_price> <target_price>"""
    parts = (update.message.text or "").split()
    if len(parts) != 4:
        await update.message.reply_text(
            "Usage: /risk <account_usd> <stop_price> <target_price>\n"
            "Example: /risk 10000 63000 70000"
        )
        return
    try:
        account = float(parts[1])
        stop = float(parts[2])
        target = float(parts[3])
    except ValueError:
        await update.message.reply_text("Bad numbers")
        return
    risk_pct = 0.02  # default 2% risk per trade
    risk_usd = account * risk_pct
    # assume entry = mid of stop/target (simplified)
    entry = (stop + target) / 2
    sl_dist = abs(entry - stop)
    tp_dist = abs(target - entry)
    if sl_dist == 0:
        await update.message.reply_text("Stop must differ from entry")
        return
    pos_size = risk_usd / sl_dist
    notional = pos_size * entry
    rr = tp_dist / sl_dist
    await update.message.reply_text(
        f"Risk calc (2% rule, entry ~ mid):\n"
        f"  Account: ${account:,.0f}\n"
        f"  Risk/trade: ${risk_usd:,.0f}\n"
        f"  Entry: {entry:.4f}\n"
        f"  Stop dist: {sl_dist:.4f}\n"
        f"  Position size: {pos_size:.4f} units (${notional:,.0f})\n"
        f"  R/R: 1:{rr:.2f}"
    )


# ============ whales / signals ============

async def cmd_whales(update, ctx):
    ws = await whales.discover_whales(limit=15)
    if not ws:
        await update.message.reply_text(
            "No whales. ARCHAM_API_KEY not set, or no data.\n"
            "Use /tier for paid tier with full whale list."
        )
        return
    lines = [f"Top whales ({len(ws)}):"]
    for w in ws[:10]:
        addr = (w.get("address") or w.get("wallet") or "?")[:10]
        pnl = w.get("pnl", 0) or w.get("pnl_usd", 0)
        wr = w.get("win_rate", 0)
        lines.append(f"`{addr}...` PnL ${pnl:,.0f} WR {wr:.0%}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_signals(update, ctx):
    ws = await whales.discover_whales(limit=30)
    if not ws:
        await update.message.reply_text("No data.")
        return
    sigs = await whales.find_signals(ws, min_buyers=2)
    if not sigs:
        await update.message.reply_text("No active signals. Next: 5 min.")
        return
    lines = [f"Whale signals ({len(sigs)}):"]
    for s in sigs[:10]:
        lines.append(
            f"`{s['token']}` buyers={s['n_buyers']} PnL=${s['total_pnl_usd']:,.0f}"
        )
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ============ AI analyze ============

async def cmd_analyze(update, ctx):
    parts = (update.message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await update.message.reply_text("Usage: /analyze BTC")
        return
    user_id = update.effective_user.id
    user = db.get_user(user_id)
    if user["tier"] == "free" and _free_limit_hit(user_id):
        await update.message.reply_text("Free limit reached. /tier")
        return
    db.increment_usage(user_id)
    sym = parts[1].strip().upper()
    cg = await market_data.markets(sym, days=7)
    if isinstance(cg, list) and cg:
        e = cg[0]
        ctx_text = (
            f"Asset: {e.get('symbol','?').upper()} {e.get('name','')}\n"
            f"Price: ${e.get('current_price')}\n"
            f"24h change: {e.get('price_change_percentage_24h'):.2f}%\n"
            f"7d change: {e.get('price_change_percentage_7d_in_currency'):.2f}%\n"
            f"Market cap: ${e.get('market_cap'):,.0f}\n"
            f"24h volume: ${e.get('total_volume'):,.0f}\n"
            f"ATH: ${e.get('ath')} ({e.get('ath_change_percentage'):.1f}% from ATH)\n"
        )
    else:
        ctx_text = f"CoinGecko error: {cg.get('error','unknown')}"
    prompt = (
        f"Asset: {sym}\n"
        f"Data:\n{ctx_text}\n\n"
        f"Give a structured trading recommendation with entry, stop-loss, "
        f"take-profit, R/R, key risks. No filler, no disclaimers."
    )
    a = adv.Advisor()
    text = await a.simple(prompt)
    await update.message.reply_text(text)


def _free_limit_hit(user_id):
    user = db.get_user(user_id)
    today = datetime.utcnow().strftime("%Y-%m-%d")
    return user["usage_date"] == today and user["usage_count"] >= FREE_DAILY_ANALYZE


# ============ free text -> journal ============

async def free_text(update, ctx):
    """Anything that's not a command becomes a journal note to 'general'."""
    if not update.message or not update.message.text:
        return
    text = update.message.text.strip()
    if not text or text.startswith("/"):
        return
    # auto-route: if starts with BTC/ETH/SOL/... treat as symbol
    words = text.split()
    sym = words[0].upper() if words[0].isalpha() and len(words[0]) <= 5 else "GENERAL"
    title = words[1] if len(words) > 1 else text[:40]
    body = text
    db.add_journal(update.effective_user.id, sym, title, body)
    await update.message.reply_text(f"Quick note saved under {sym}.")


# ============ tier ============

async def cmd_tier(update, ctx):
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("Pro $9/mo", callback_data="tier:pro"),
        InlineKeyboardButton("Whale $49/mo", callback_data="tier:whale"),
    ]])
    await update.message.reply_text(
        "Tiers:\n\n"
        "Pro $9/mo - unlimited analyze, watchlist alerts\n"
        "Whale $49/mo - full whale list, copy signals\n\n"
        "Payment via Telegram Stars (soon).",
        reply_markup=kb,
    )


async def tier_picked(update, ctx):
    q = update.callback_query
    await q.answer()
    t = q.data.split(":", 1)[1]
    if t == "help":
        await cmd_help.__wrapped__(q.message, ctx) if False else None
        await q.message.reply_text(
            "/price /watch /buy /sell /close /portfolio /note /risk /whales /signals /analyze"
        )
    elif t == "pro":
        await q.edit_message_text("Pro $9/mo - DM @guiboratto50 for early access.")
    else:
        await q.edit_message_text("Whale $49/mo - DM @guiboratto50.")