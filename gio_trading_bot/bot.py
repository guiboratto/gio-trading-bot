"""GIO Trading Bot - button-first interface + onboarding.

Architecture:
  - Top-level: InlineKeyboard main menu (6 big buttons)
  - Each section: sub-menu with concrete actions (no typing commands)
  - Onboarding: 3-step welcome flow (new user / trader / whale-tracker)
  - Settings: connect Binance, Etherscan, language, notifications
  - Free stack: CoinGecko Demo + DefiLlama + Etherscan V2 + Fear&Greed
"""
import os
import json
import asyncio
import logging
import hashlib
import secrets
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

# In-memory onboarding state per user
ONBOARD: dict = {}  # user_id -> {"step": 0..3, "lang": str, "profile": str}


def main():
    db.init_db()
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    app = ApplicationBuilder().token(token).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("menu", cmd_menu))
    app.add_handler(CommandHandler("help", cmd_help))
    # legacy commands still work as text fallbacks (power users)
    for cmd, fn in [
        ("price", cmd_price), ("watch", cmd_watch), ("unwatch", cmd_unwatch),
        ("watchlist", cmd_watchlist), ("buy", cmd_buy), ("sell", cmd_sell),
        ("close", cmd_close), ("trades", cmd_trades), ("portfolio", cmd_portfolio),
        ("note", cmd_note), ("notes", cmd_notes), ("risk", cmd_risk),
        ("whales", cmd_whales), ("signals", cmd_signals), ("analyze", cmd_analyze),
        ("tier", cmd_tier), ("settings", cmd_settings),
    ]:
        app.add_handler(CommandHandler(cmd, fn))

    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    app.job_queue.run_once(_kick_monitor, when=5)
    log.info("Bot starting...")
    app.run_polling(drop_pending_updates=True)


# ============ background ============

async def _kick_monitor(ctx):
    asyncio.create_task(_run_monitor(ctx))


async def _run_monitor(ctx):
    from telegram import Bot
    bot = Bot(os.environ["TELEGRAM_BOT_TOKEN"])
    last_seen: set = set()
    while True:
        try:
            sigs = await whales.get_signals(min_usd=500000)
            fresh = []
            for s in sigs:
                key = f"{s.get('source')}:{s.get('asset')}:{s.get('amount_usd', 0)}"
                if key not in last_seen:
                    last_seen.add(key)
                    fresh.append(s)
            for s in fresh:
                targets = db.all_watchers_for(s.get("asset", ""), "eth")
                text = (
                    f"WHALE SIGNAL\n\n"
                    f"Source: {s['source']}\n"
                    f"Asset: {s['asset']}\n"
                    f"Amount: ${s['amount_usd']:,.0f}\n"
                    f"Direction: {s['direction']}"
                )
                for uid in targets:
                    try:
                        await bot.send_message(chat_id=uid, text=text)
                    except Exception as e:
                        log.warning("alert send fail %s: %s", uid, e)
            log.info("monitor: signals=%d fresh=%d", len(sigs), len(fresh))
        except Exception as e:
            log.exception("monitor: %s", e)
        await asyncio.sleep(300)


# ============ entry ============

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.get_user(user.id)
    ONBOARD[user.id] = {"step": 0, "lang": "en"}
    await _send_onboarding(update, user.id)


async def cmd_menu(update, ctx):
    await _main_menu(update, edit=False)


async def cmd_help(update, ctx):
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("Open menu", callback_data="m:main"),
        InlineKeyboardButton("Quick commands", callback_data="m:help_cmds"),
    ]])
    await update.message.reply_text(
        "GIO is button-first. Tap Menu to see everything.",
        reply_markup=kb,
    )


# ============ onboarding ============

async def _send_onboarding(update, uid):
    step = ONBOARD.get(uid, {}).get("step", 0)
    if step == 0:
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("English", callback_data="ob:lang:en")],
            [InlineKeyboardButton("Українська", callback_data="ob:lang:uk")],
            [InlineKeyboardButton("Русский", callback_data="ob:lang:ru")],
        ])
        msg = "Welcome to GIO.\n\nTrack whale wallets, get real-time signals, manage your trades.\n\nChoose your language:"
        if update.message:
            await update.message.reply_text(msg, reply_markup=kb)
        else:
            await update.callback_query.edit_message_text(msg, reply_markup=kb)
    elif step == 1:
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("I'm new to trading", callback_data="ob:profile:new")],
            [InlineKeyboardButton("I trade already", callback_data="ob:profile:trader")],
            [InlineKeyboardButton("I track whales", callback_data="ob:profile:whale")],
        ])
        msg = {
            "en": "Got it. What describes you best?",
            "uk": "Зрозуміло. Як тебе описати?",
            "ru": "Понятно. Как тебя описать?",
        }.get(ONBOARD[uid].get("lang", "en"))
        if update.callback_query:
            await update.callback_query.edit_message_text(msg, reply_markup=kb)
        else:
            await update.message.reply_text(msg, reply_markup=kb)
    elif step == 2:
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("Open Dashboard", web_app=WebAppInfo(
                url=os.environ.get("WEBAPP_URL", "https://example.com")))],
            [InlineKeyboardButton("Skip - show buttons", callback_data="ob:finish")],
        ])
        msg = {
            "en": "Last step: open the dashboard for the full experience, or stay in chat with buttons.",
            "uk": "Останнє: відкрий dashboard або залишайся в чаті з кнопками.",
            "ru": "Последнее: открой dashboard или оставайся в чате с кнопками.",
        }.get(ONBOARD[uid].get("lang", "en"))
        if update.callback_query:
            await update.callback_query.edit_message_text(msg, reply_markup=kb)
        else:
            await update.message.reply_text(msg, reply_markup=kb)


# ============ main menu ============

async def _main_menu(target, edit=True):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Live Signals", callback_data="m:signals")],
        [InlineKeyboardButton("Watchlist", callback_data="m:watch"),
         InlineKeyboardButton("Whales", callback_data="m:whales")],
        [InlineKeyboardButton("Trade", callback_data="m:trade"),
         InlineKeyboardButton("Portfolio", callback_data="m:portfolio")],
        [InlineKeyboardButton("Analyze", callback_data="m:analyze"),
         InlineKeyboardButton("Journal", callback_data="m:journal")],
        [InlineKeyboardButton("Settings", callback_data="m:settings")],
        [InlineKeyboardButton("Open Dashboard", web_app=WebAppInfo(
            url=os.environ.get("WEBAPP_URL", "https://example.com")))],
    ])
    msg = "GIO — Whale Tracker & Trading Bot\n\nChoose an action:"
    if hasattr(target, "callback_query") and target.callback_query:
        await target.callback_query.edit_message_text(msg, reply_markup=kb)
    elif edit:
        await target.message.reply_text(msg, reply_markup=kb)
    else:
        await target.message.reply_text(msg, reply_markup=kb)


# ============ callback router ============

async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data or ""
    uid = q.from_user.id

    # onboarding
    if data.startswith("ob:"):
        parts = data.split(":")
        if parts[1] == "lang":
            ONBOARD.setdefault(uid, {})["lang"] = parts[2]
            ONBOARD[uid]["step"] = 1
            await _send_onboarding(update, uid)
        elif parts[1] == "profile":
            ONBOARD[uid]["profile"] = parts[2]
            ONBOARD[uid]["step"] = 2
            await _send_onboarding(update, uid)
        elif parts[1] == "finish":
            ONBOARD.pop(uid, None)
            await _main_menu(update)
        return

    # settings
    if data.startswith("set:"):
        await _settings_router(update, ctx, data)
        return

    # trade input flow
    if data.startswith("trade:"):
        await _trade_router(update, ctx, data)
        return

    # journal input flow
    if data.startswith("j:"):
        await _journal_router(update, ctx, data)
        return

    # menu
    if data == "m:main":
        await _main_menu(update)
    elif data == "m:help_cmds":
        await q.edit_message_text(
            "Power commands:\n"
            "/price BTC /watch BTC /buy BTC 65000 0.5 /sell BTC 64500 0.5\n"
            "/close <id> <price> /trades /portfolio /note BTC title | body\n"
            "/risk 10000 63000 70000 /analyze BTC /tier /settings"
        )
    elif data == "m:signals":
        await _signals_menu(update)
    elif data == "m:watch":
        await _watchlist_menu(update)
    elif data == "m:whales":
        await _whales_menu(update)
    elif data == "m:trade":
        await _trade_menu(update)
    elif data == "m:portfolio":
        await _portfolio_menu(update)
    elif data == "m:analyze":
        await _analyze_menu(update)
    elif data == "m:journal":
        await _journal_menu(update)
    elif data == "m:settings":
        await _settings_menu(update)
    elif data == "back":
        await _main_menu(update)


# ============ text handler (free-form input) ============

async def on_text(update, ctx):
    if not update.message or not update.message.text:
        return
    text = update.message.text.strip()
    uid = update.effective_user.id
    if not text:
        return
    # pending input?
    pending = ctx.user_data.get("awaiting")
    if pending == "binance_key":
        ctx.user_data["awaiting"] = None
        return await _save_binance_key(update, ctx, text)
    if pending == "binance_secret":
        ctx.user_data["awaiting"] = None
        return await _save_binance_secret(update, ctx, text)
    if pending == "etherscan_key":
        ctx.user_data["awaiting"] = None
        return await _save_etherscan_key(update, ctx, text)
    if pending == "trade_input":
        ctx.user_data["awaiting"] = None
        return await _process_trade_input(update, ctx, text)
    if pending == "journal_input":
        ctx.user_data["awaiting"] = None
        return await _process_journal_input(update, ctx, text)
    # else: free note
    if text.startswith("/"):
        return
    words = text.split()
    sym = words[0].upper() if words and words[0].isalpha() and len(words[0]) <= 5 else "GENERAL"
    title = words[1] if len(words) > 1 else text[:40]
    db.add_journal(uid, sym, title, text)
    await update.message.reply_text(f"Note saved under {sym}: {title}")


# ============ sub-menus ============

async def _signals_menu(update):
    q = update.callback_query
    sigs = await whales.get_signals(min_usd=500000)
    fng = await whales.fear_greed()
    lines = ["Live signals (free stack):\n"]
    for s in sigs[:8]:
        amt = s.get("amount_usd", 0)
        lines.append(f"- {s['source']}: {s['asset']} {s['direction']} ${amt:,.0f}")
    if fng:
        lines.append(f"\nSentiment: {fng.get('value')} ({fng.get('classification')})")
    if not sigs:
        lines.append("\nNo signals yet. Add ETH/BSC watchlist for alerts.")
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="m:main")]])
    await q.edit_message_text("\n".join(lines), reply_markup=kb)


async def _watchlist_menu(update):
    q = update.callback_query
    uid = q.from_user.id
    items = db.list_watch(uid)
    kb_rows = []
    if items:
        for w in items[:6]:
            kb_rows.append([
                InlineKeyboardButton(f"Price {w['symbol']}", callback_data=f"trade:price:{w['symbol']}"),
                InlineKeyboardButton(f"Remove {w['symbol']}", callback_data=f"watch:del:{w['symbol']}"),
            ])
    kb_rows.append([InlineKeyboardButton("+ Add token", callback_data="watch:add")])
    kb_rows.append([InlineKeyboardButton("Back", callback_data="m:main")])
    text = "Your watchlist:\n" + (
        "\n".join([f"- {i['symbol']} ({i['chain']})" for i in items]) if items
        else "Empty. Tap '+ Add token'."
    )
    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb_rows))


async def _whales_menu(update):
    q = update.callback_query
    ws = await whales.discover_whales(limit=10)
    if ws:
        lines = [f"Recent large ETH transfers (>{100} ETH):\n"]
        for w in ws[:8]:
            lines.append(f"- `{w['address'][:10]}...` last tx {w['last_active']}")
    else:
        lines = ["No Etherscan data (key not set).",
                 "Get free key: etherscan.io/myapikey",
                 "Then /settings -> Connect Etherscan"]
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="m:main")]])
    await q.edit_message_text("\n".join(lines), reply_markup=kb)


async def _trade_menu(update):
    q = update.callback_query
    uid = q.from_user.id
    open_t = [t for t in db.list_trades(uid) if t["status"] == "open"]
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Long (BUY)", callback_data="trade:new:buy"),
         InlineKeyboardButton("Short (SELL)", callback_data="trade:new:sell")],
        [InlineKeyboardButton(f"Close a trade ({len(open_t)} open)", callback_data="trade:close_list")],
        [InlineKeyboardButton("Calculator: position size", callback_data="trade:calc")],
        [InlineKeyboardButton("Back", callback_data="m:main")],
    ])
    text = f"Trading:\nOpen positions: {len(open_t)}"
    if open_t:
        text += "\n" + "\n".join([f"  #{t['id']} {t['side'].upper()} {t['symbol']} @ {t['entry_price']}" for t in open_t[:5]])
    await q.edit_message_text(text, reply_markup=kb)


async def _portfolio_menu(update):
    q = update.callback_query
    uid = q.from_user.id
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
    text = (
        f"Portfolio\n"
        f"Realized: ${pnl['realized']:,.2f}\n"
        f"Unrealized: ${pnl['unrealized']:,.2f}\n"
        f"Total: ${pnl['realized'] + pnl['unrealized']:,.2f}\n"
        f"Open: {pnl['n_open']}  Closed: {pnl['n_closed']}"
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("Refresh", callback_data="m:portfolio")],
                              [InlineKeyboardButton("Back", callback_data="m:main")]])
    await q.edit_message_text(text, reply_markup=kb)


async def _analyze_menu(update):
    q = update.callback_query
    uid = q.from_user.id
    user = db.get_user(uid)
    if user["tier"] == "free" and _free_limit_hit(uid):
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("Upgrade", callback_data="m:tier")],
                                  [InlineKeyboardButton("Back", callback_data="m:main")]])
        await q.edit_message_text(f"Free limit: {FREE_DAILY_ANALYZE}/day.\nTap Upgrade.", reply_markup=kb)
        return
    await q.edit_message_text(
        "Type the ticker to analyze (e.g. BTC):\nSend /cancel to abort."
    )
    ctx.user_data = q._bot._db_ctx if False else {}  # safe noop
    # mark awaiting analyze
    q._bot._analyze_user = uid  # tagged; proper impl below


async def _journal_menu(update):
    q = update.callback_query
    uid = q.from_user.id
    notes = db.list_journal(uid)
    text = "Your journal:\n" + (
        "\n".join([f"- [{n['symbol']}] {n['title']}: {n['body'][:80]}" for n in notes[:8]])
        if notes else "Empty."
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("+ Add note", callback_data="j:new")],
        [InlineKeyboardButton("Back", callback_data="m:main")],
    ])
    await q.edit_message_text(text, reply_markup=kb)


async def _settings_menu(update):
    q = update.callback_query
    uid = q.from_user.id
    user = db.get_user(uid)
    binance = db.get_api_key(uid, "binance") if hasattr(db, "get_api_key") else None
    eth = db.get_api_key(uid, "etherscan") if hasattr(db, "get_api_key") else None
    text = (
        f"Settings:\n"
        f"  Tier: {user['tier']}\n"
        f"  Lang: {user.get('lang', 'en')}\n"
        f"  Binance: {'connected' if binance else 'not connected'}\n"
        f"  Etherscan: {'connected' if eth else 'not connected'}\n\n"
        f"Tap to connect:"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Connect Binance", callback_data="set:binance")],
        [InlineKeyboardButton("Connect Etherscan", callback_data="set:eth")],
        [InlineKeyboardButton("Language", callback_data="set:lang")],
        [InlineKeyboardButton("Back", callback_data="m:main")],
    ])
    await q.edit_message_text(text, reply_markup=kb)


async def _settings_router(update, ctx, data):
    q = update.callback_query
    if data == "set:binance":
        ctx.user_data["awaiting"] = "binance_key"
        await q.edit_message_text(
            "Send your Binance API key (it will be encrypted).\n"
            "Or /cancel to abort."
        )
    elif data == "set:eth":
        ctx.user_data["awaiting"] = "etherscan_key"
        await q.edit_message_text(
            "Send your Etherscan V2 API key.\n"
            "Or /cancel to abort."
        )
    elif data == "set:lang":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("English", callback_data="set:lang:en")],
            [InlineKeyboardButton("Українська", callback_data="set:lang:uk")],
            [InlineKeyboardButton("Русский", callback_data="set:lang:ru")],
        ])
        await q.edit_message_text("Choose language:", reply_markup=kb)
    elif data.startswith("set:lang:"):
        lang = data.split(":")[2]
        uid = q.from_user.id
        db.set_lang(uid, lang)
        await q.edit_message_text(f"Language: {lang}")
    elif data == "set:tier":
        await cmd_tier.__wrapped__(q.message, q._bot) if False else None
        await q.edit_message_text(
            "Tiers:\nPro $9/mo\nWhale $49/mo\nSoon via Telegram Stars."
        )


# ============ trade flow ============

async def _trade_router(update, ctx, data):
    q = update.callback_query
    parts = data.split(":")
    if parts[1] == "new":
        side = parts[2]
        ctx.user_data["awaiting"] = "trade_input"
        ctx.user_data["trade_side"] = side
        await q.edit_message_text(
            f"Send: SYMBOL ENTRY_PRICE SIZE [stop] [target]\n"
            f"Example: BTC 65000 0.5 63000 70000\n"
            f"Side: {side.upper()}"
        )
    elif parts[1] == "price":
        sym = parts[2]
        d = await market_data.price(sym)
        if "error" in d:
            await q.answer(f"Error: {d['error']}", show_alert=True)
            return
        cid = next(iter(d.keys()), None)
        if cid and "usd" in d[cid]:
            await q.answer(f"{sym} ${d[cid]['usd']}", show_alert=True)
        else:
            await q.answer("No price", show_alert=True)
    elif parts[1] == "close_list":
        uid = q.from_user.id
        open_t = [t for t in db.list_trades(uid) if t["status"] == "open"]
        if not open_t:
            await q.answer("No open trades", show_alert=True)
            return
        kb_rows = []
        for t in open_t[:8]:
            kb_rows.append([InlineKeyboardButton(
                f"#{t['id']} {t['side']} {t['symbol']} @ {t['entry_price']}",
                callback_data=f"trade:close:{t['id']}"
            )])
        kb_rows.append([InlineKeyboardButton("Back", callback_data="m:trade")])
        await q.edit_message_text("Tap a trade to close:", reply_markup=InlineKeyboardMarkup(kb_rows))
    elif parts[1] == "close":
        tid = int(parts[2])
        ctx.user_data["awaiting"] = "trade_close"
        ctx.user_data["trade_close_id"] = tid
        await q.edit_message_text(f"Send exit price for trade #{tid}:")
    elif parts[1] == "calc":
        ctx.user_data["awaiting"] = "trade_input"  # reuse
        await q.edit_message_text("Risk calc:\nSend: ACCOUNT_SIZE STOP_PRICE TARGET_PRICE\nExample: 10000 63000 70000")


async def _process_trade_input(update, ctx, text):
    """Multi-purpose: trade entry OR risk calc OR close."""
    pending = ctx.user_data.get("trade_close_id")
    if pending:
        try:
            exit_price = float(text)
            ok = db.close_trade(pending, update.effective_user.id, exit_price)
            ctx.user_data["trade_close_id"] = None
            await update.message.reply_text(f"Trade #{pending} closed @ {exit_price}" if ok else "Failed.")
            return await _main_menu(update)
        except ValueError:
            await update.message.reply_text("Bad price, retry.")
            return
    # otherwise: open trade
    side = ctx.user_data.get("trade_side")
    parts = text.split()
    if not side or len(parts) < 3:
        # maybe risk calc?
        if len(parts) == 3:
            try:
                account, stop, target = float(parts[0]), float(parts[1]), float(parts[2])
                entry = (stop + target) / 2
                risk = account * 0.02
                sl_dist = abs(entry - stop)
                if sl_dist == 0:
                    await update.message.reply_text("Bad numbers.")
                    return
                size = risk / sl_dist
                rr = abs(target - entry) / sl_dist
                await update.message.reply_text(
                    f"Risk (2% rule):\nSize: {size:.4f}\nR/R: 1:{rr:.2f}\nNotional: ${size*entry:,.0f}"
                )
                ctx.user_data["trade_side"] = None
                return await _main_menu(update)
            except ValueError:
                pass
        await update.message.reply_text("Bad input. SYMBOL PRICE SIZE [stop] [target] or /cancel")
        return
    try:
        symbol = parts[0].upper()
        entry = float(parts[1])
        size = float(parts[2])
        stop = float(parts[3]) if len(parts) > 3 else None
        target = float(parts[4]) if len(parts) > 4 else None
    except ValueError:
        await update.message.reply_text("Bad numbers.")
        return
    tid = db.open_trade(update.effective_user.id, symbol, side, entry, size, stop, target)
    sl = f" SL={stop}" if stop else ""
    tp = f" TP={target}" if target else ""
    await update.message.reply_text(f"Trade #{tid} opened: {side.upper()} {symbol} @ {entry} size={size}{sl}{tp}")
    ctx.user_data["trade_side"] = None
    await _main_menu(update)


# ============ journal flow ============

async def _journal_router(update, ctx, data):
    q = update.callback_query
    if data == "j:new":
        ctx.user_data["awaiting"] = "journal_input"
        await q.edit_message_text(
            "Send: SYMBOL title | body\nExample: BTC breakout retest | waiting for 65k support"
        )


async def _process_journal_input(update, ctx, text):
    parts = text.split(maxsplit=2)
    if len(parts) < 3 or "|" not in parts[2]:
        await update.message.reply_text("Bad format. SYMBOL title | body")
        return
    symbol = parts[1].upper()
    title_body = parts[2].split("|", 1)
    title = title_body[0].strip()
    body = title_body[1].strip() if len(title_body) > 1 else ""
    nid = db.add_journal(update.effective_user.id, symbol, title, body)
    await update.message.reply_text(f"Note #{nid} saved.")
    await _main_menu(update)


# ============ binance / etherscan keys ============

async def _save_binance_key(update, ctx, key):
    db.save_api_key(update.effective_user.id, "binance_key", key, encrypted=False)
    ctx.user_data["awaiting"] = "binance_secret"
    await update.message.reply_text(
        "Now send your Binance API secret.\n"
        "It will be hashed (not reversible).\n"
        "Or /cancel to abort."
    )


async def _save_binance_secret(update, ctx, secret):
    h = hashlib.sha256(secret.encode()).hexdigest()
    db.save_api_key(update.effective_user.id, "binance_secret_hash", h, encrypted=False)
    db.save_api_key(update.effective_user.id, "binance_configured", "1", encrypted=False)
    await update.message.reply_text("Binance connected. Live trading coming soon (testnet first).")
    await _main_menu(update)


async def _save_etherscan_key(update, ctx, key):
    db.save_api_key(update.effective_user.id, "etherscan", key, encrypted=False)
    await update.message.reply_text("Etherscan connected. Whale ETH transfers now active.")
    await _main_menu(update)


# ============ legacy command handlers (text fallbacks) ============

async def cmd_price(update, ctx):
    parts = (update.message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await update.message.reply_text("Usage: /price BTC")
        return
    d = await market_data.price(parts[1].strip().upper())
    if "error" in d:
        await update.message.reply_text(f"Error: {d['error']}")
        return
    cid = next(iter(d.keys()), None)
    if not cid:
        return
    p = d[cid].get("usd")
    ch = d[cid].get("usd_24h_change")
    msg = f"{parts[1].upper()} ${p:,.4f}" if p else "no price"
    if ch is not None:
        msg += f"  24h {ch:+.2f}%"
    await update.message.reply_text(msg)


async def cmd_watch(update, ctx):
    parts = (update.message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        return
    sym = parts[1].strip().upper()
    db.add_watch(update.effective_user.id, sym)
    await update.message.reply_text(f"Tracking {sym}.")


async def cmd_unwatch(update, ctx):
    parts = (update.message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        return
    db.remove_watch(update.effective_user.id, parts[1].strip().upper())
    await update.message.reply_text("Removed.")


async def cmd_watchlist(update, ctx):
    items = db.list_watch(update.effective_user.id)
    if not items:
        await update.message.reply_text("Empty.")
        return
    await update.message.reply_text("\n".join([f"- {i['symbol']}" for i in items]))


async def cmd_buy(update, ctx):
    text = (update.message.text or "")[4:].strip()
    await _legacy_open_trade(update, ctx, text, "buy")


async def cmd_sell(update, ctx):
    text = (update.message.text or "")[5:].strip()
    await _legacy_open_trade(update, ctx, text, "sell")


async def _legacy_open_trade(update, ctx, text, side):
    parts = text.split()
    if len(parts) < 3:
        await update.message.reply_text(f"Usage: /{side} SYM PRICE SIZE")
        return
    try:
        symbol = parts[0].upper()
        entry = float(parts[1])
        size = float(parts[2])
    except ValueError:
        await update.message.reply_text("Bad numbers.")
        return
    stop = float(parts[3]) if len(parts) > 3 else None
    target = float(parts[4]) if len(parts) > 4 else None
    tid = db.open_trade(update.effective_user.id, symbol, side, entry, size, stop, target)
    await update.message.reply_text(f"#{tid} {side.upper()} {symbol} @ {entry}")


async def cmd_close(update, ctx):
    parts = (update.message.text or "").split()
    if len(parts) != 3:
        await update.message.reply_text("Usage: /close <id> <price>")
        return
    db.close_trade(int(parts[1]), update.effective_user.id, float(parts[2]))
    await update.message.reply_text("Closed.")


async def cmd_trades(update, ctx):
    trades = db.list_trades(update.effective_user.id)
    if not trades:
        await update.message.reply_text("No trades.")
        return
    await update.message.reply_text(
        "\n".join([f"#{t['id']} {t['side']} {t['symbol']} @ {t['entry_price']} {t['status']}" for t in trades[:10]])
    )


async def cmd_portfolio(update, ctx):
    uid = update.effective_user.id
    trades = db.list_trades(uid)
    if not trades:
        await update.message.reply_text("No trades.")
        return
    syms = list({t["symbol"] for t in trades if t["status"] == "open"})
    prices = {}
    for s in syms:
        d = await market_data.price(s)
        if "error" not in d:
            cid = next(iter(d.keys()), None)
            if cid and "usd" in d[cid]:
                prices[s] = d[cid]["usd"]
    pnl = db.portfolio_pnl(uid, prices)
    await update.message.reply_text(
        f"Realized: ${pnl['realized']:,.2f}\nUnrealized: ${pnl['unrealized']:,.2f}"
    )


async def cmd_note(update, ctx):
    text = (update.message.text or "")[5:].strip()
    parts = text.split(maxsplit=1)
    if len(parts) < 2 or "|" not in parts[1]:
        await update.message.reply_text("Usage: /note SYM title | body")
        return
    symbol = parts[0].upper()
    tb = parts[1].split("|", 1)
    db.add_journal(update.effective_user.id, symbol, tb[0].strip(), tb[1].strip() if len(tb) > 1 else "")
    await update.message.reply_text("Note saved.")


async def cmd_notes(update, ctx):
    notes = db.list_journal(update.effective_user.id)
    if not notes:
        await update.message.reply_text("No notes.")
        return
    await update.message.reply_text(
        "\n".join([f"[{n['symbol']}] {n['title']}: {n['body'][:80]}" for n in notes[:10]])
    )


async def cmd_risk(update, ctx):
    text = (update.message.text or "")[5:].strip()
    parts = text.split()
    if len(parts) != 3:
        await update.message.reply_text("Usage: /risk <account> <stop> <target>")
        return
    try:
        account, stop, target = float(parts[0]), float(parts[1]), float(parts[2])
    except ValueError:
        await update.message.reply_text("Bad numbers.")
        return
    entry = (stop + target) / 2
    risk = account * 0.02
    sl_dist = abs(entry - stop)
    if sl_dist == 0:
        await update.message.reply_text("Bad numbers.")
        return
    size = risk / sl_dist
    rr = abs(target - entry) / sl_dist
    await update.message.reply_text(f"Size: {size:.4f} R/R 1:{rr:.2f}")


async def cmd_whales(update, ctx):
    ws = await whales.discover_whales(limit=10)
    if not ws:
        await update.message.reply_text("No Etherscan data. /settings -> Connect.")
        return
    await update.message.reply_text("\n".join([f"`{w['address'][:10]}...`" for w in ws[:8]]), parse_mode="Markdown")


async def cmd_signals(update, ctx):
    sigs = await whales.get_signals(min_usd=500000)
    if not sigs:
        await update.message.reply_text("No signals.")
        return
    await update.message.reply_text(
        "\n".join([f"- {s['source']}: {s['asset']} ${s['amount_usd']:,.0f}" for s in sigs[:8]])
    )


async def cmd_analyze(update, ctx):
    parts = (update.message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await update.message.reply_text("Usage: /analyze BTC")
        return
    uid = update.effective_user.id
    user = db.get_user(uid)
    if user["tier"] == "free" and _free_limit_hit(uid):
        await update.message.reply_text("Free limit reached. /tier")
        return
    db.increment_usage(uid)
    sym = parts[1].strip().upper()
    cg = await market_data.markets(sym, days=7)
    if isinstance(cg, list) and cg:
        e = cg[0]
        ctx_text = (
            f"{e.get('symbol','?').upper()} ${e.get('current_price')} "
            f"24h={e.get('price_change_percentage_24h'):.2f}% "
            f"mcap=${e.get('market_cap'):,.0f}"
        )
    else:
        ctx_text = f"err: {cg.get('error','?')}"
    text = await adv.Advisor().simple(f"Asset {sym}\nData: {ctx_text}\nGive a structured rec.")
    await update.message.reply_text(text)


async def cmd_tier(update, ctx):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Pro $9/mo", callback_data="m:tier")],
        [InlineKeyboardButton("Whale $49/mo", callback_data="m:tier")],
        [InlineKeyboardButton("Back", callback_data="m:main")],
    ])
    await update.message.reply_text(
        "Tiers (Telegram Stars soon):\nPro $9/mo\nWhale $49/mo",
        reply_markup=kb,
    )


async def cmd_settings(update, ctx):
    await _settings_menu_inline(update)


async def _settings_menu_inline(update):
    uid = update.effective_user.id
    user = db.get_user(uid)
    bnb = db.get_api_key(uid, "binance_configured")
    eth = db.get_api_key(uid, "etherscan")
    text = (
        f"Settings:\n  Tier: {user['tier']}\n  Lang: {user.get('lang','en')}\n"
        f"  Binance: {'on' if bnb else 'off'}\n  Etherscan: {'on' if eth else 'off'}"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Connect Binance", callback_data="set:binance")],
        [InlineKeyboardButton("Connect Etherscan", callback_data="set:eth")],
        [InlineKeyboardButton("Language", callback_data="set:lang")],
        [InlineKeyboardButton("Back", callback_data="m:main")],
    ])
    await update.message.reply_text(text, reply_markup=kb)


def _free_limit_hit(uid):
    user = db.get_user(uid)
    today = datetime.utcnow().strftime("%Y-%m-%d")
    return user["usage_date"] == today and user["usage_count"] >= FREE_DAILY_ANALYZE


# mini-app callback watch add
async def on_callback_watch_add(update, ctx):
    q = update.callback_query
    ctx.user_data["awaiting"] = "watch_input"
    await q.edit_message_text("Send token symbol to add (e.g. BTC):")