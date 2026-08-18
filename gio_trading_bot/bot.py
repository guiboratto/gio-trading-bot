"""GIO Trading Bot - Whale Tracker edition."""
import os
import json
import asyncio
import logging
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ConversationHandler, filters, ContextTypes,
)
from .advisor import Advisor
from .market_data import coingecko, debank as md_debank, archam as md_archam
from . import archam, debank as db_debank, whales
from .db import init_db, get_user, set_user_tier, increment_usage, save_history

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("gio")

# Conversation states
STRATEGY, ASSET, HORIZON, ENTRY, RISK, FOLLOWUPS = range(6)
FREE_DAILY_LIMIT = 5
FREE_SIGNALS_DELAY_SEC = 3600  # 1h delay for free users

# In-memory cache (replace with redis/postgres later)
SIGNAL_CACHE: list = []  # list of dicts: token, buyers, n_buyers, total_pnl_usd, detected_at


def main():
    init_db()
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    app = ApplicationBuilder().token(token).build()
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", cmd_start)],
        states={
            STRATEGY: [CallbackQueryHandler(strategy_picked, pattern="^strat:")],
            ASSET:    [MessageHandler(filters.TEXT & ~filters.COMMAND, asset_picked)],
            HORIZON:  [CallbackQueryHandler(horizon_picked, pattern="^hor:")],
            ENTRY:    [MessageHandler(filters.TEXT & ~filters.COMMAND, entry_picked)],
            RISK:     [CallbackQueryHandler(risk_picked, pattern="^risk:")],
            FOLLOWUPS:[MessageHandler(filters.TEXT & ~filters.COMMAND, followup)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    )
    app.add_handler(conv)
    app.add_handler(CommandHandler("analyze", cmd_analyze))
    app.add_handler(CommandHandler("whales", cmd_whales))
    app.add_handler(CommandHandler("signals", cmd_signals))
    app.add_handler(CommandHandler("track", cmd_track))
    app.add_handler(CommandHandler("tier", cmd_tier))
    app.add_handler(CallbackQueryHandler(tier_picked, pattern="^tier:"))
    app.add_handler(CommandHandler("help", cmd_help))

    # background scheduler
    app.job_queue.run_once(_kick_monitor, when=5)
    log.info("Bot starting...")
    app.run_polling(drop_pending_updates=True)


async def _kick_monitor(ctx: ContextTypes.DEFAULT_TYPE):
    """Spawn background monitor loop. Runs once at startup."""
    asyncio.create_task(_run_monitor(ctx))


async def _run_monitor(ctx: ContextTypes.DEFAULT_TYPE):
    """Discover whales every 5 min and refresh SIGNAL_CACHE."""
    while True:
        try:
            ws = await whales.discover_whales(limit=30)
            if ws:
                sigs = await whales.find_signals(ws, min_buyers=3)
                global SIGNAL_CACHE
                SIGNAL_CACHE = sigs
                log.info("signals updated: %d", len(sigs))
        except Exception as e:
            log.exception("monitor: %s", e)
        await asyncio.sleep(300)


# ========== commands ==========

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Whale Tracker", callback_data="strat:whale")],
        [InlineKeyboardButton("Trading Advisor", callback_data="strat:long")],
    ])
    await update.message.reply_text(
        "GIO - smart money signals.\n\n"
        "We track top-30 crypto whales (Archam) and detect when they "
        "buy the same token -> early signal.\n\n"
        "Choose mode:",
        reply_markup=kb,
    )
    return STRATEGY


async def strategy_picked(update, ctx):
    q = update.callback_query
    await q.answer()
    mode = q.data.split(":", 1)[1]
    if mode == "whale":
        await q.edit_message_text("Whale mode. Commands: /whales /signals /track <token>")
        return ConversationHandler.END
    ctx.user_data["strategy"] = mode
    await q.edit_message_text("Enter asset ticker (BTC, ETH, AAPL):")
    return ASSET


async def cmd_whales(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Show top whales we are tracking."""
    ws = await whales.discover_whales(limit=20)
    if not ws:
        await update.message.reply_text(
            "No whales yet. ARCHAM_API_KEY not set or API error.\n"
            "Free users see a sample list. Pro gets full PnL + win-rate."
        )
        return
    lines = ["TOP WHALES (Archam)\n"]
    for w in ws[:10]:
        addr = (w.get("address") or w.get("wallet") or "?")[:10]
        pnl = w.get("pnl", 0) or w.get("pnl_usd", 0)
        wr = w.get("win_rate", 0)
        lines.append(f"`{addr}...` PnL ${pnl:,.0f} WR {wr:.0%}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_signals(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Current signals: tokens bought by >=3 whales in last 24h."""
    user_id = update.effective_user.id
    user = get_user(user_id)
    is_pro = user["tier"] == "pro"

    if not SIGNAL_CACHE:
        await update.message.reply_text(
            "No active signals yet. Monitor runs every 5 min.\n"
            "Next: /signals in 5 min, or /track <token> for watchlist."
        )
        return

    sigs = SIGNAL_CACHE[:10]
    lines = [f"WHALE SIGNALS (n={len(sigs)})\n"]
    for s in sigs:
        delay = "" if is_pro else f"  (delay {FREE_SIGNALS_DELAY_SEC//60}min for free)"
        lines.append(
            f"*Token:* `{s['token']}`\n"
            f"*Buyers:* {s['n_buyers']} whales, "
            f"total PnL ${s['total_pnl_usd']:,.0f}{delay}"
        )
    if not is_pro:
        lines.append("\n_Pro tier: real-time + watchlist alerts._ /tier")
    await update.message.reply_text("\n\n".join(lines), parse_mode="Markdown")


async def cmd_track(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Add token to watchlist. Pro only for alerts."""
    parts = (update.message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await update.message.reply_text("Usage: /track BTC")
        return
    user_id = update.effective_user.id
    user = get_user(user_id)
    symbol = parts[1].upper()
    if user["tier"] != "pro":
        await update.message.reply_text(
            f"Watchlist alerts are Pro feature.\n"
            f"/tier - $49/mo for real-time whale alerts."
        )
        return
    # in production: insert into watchlist table
    await update.message.reply_text(f"Tracking {symbol}. Alerts when whales move.")


async def cmd_analyze(update, ctx):
    parts = (update.message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await update.message.reply_text("Usage: /analyze BTC")
        return
    user_id = update.effective_user.id
    if _limit_reached(user_id):
        return await _show_paywall(update, ctx)
    increment_usage(user_id)
    ctx.user_data.clear()
    ctx.user_data.update(strategy="long", asset=parts[1].upper(),
                         horizon="1m", entry="market", risk="med")
    await update.message.reply_text(f"Analyzing {ctx.user_data['asset']}...")
    return await _analyze(update, ctx)


async def cmd_tier(update, ctx):
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("Pro $49/mo", callback_data="tier:pro"),
        InlineKeyboardButton("Whale $199/mo", callback_data="tier:whale"),
        InlineKeyboardButton("Signal $0.50", callback_data="tier:signal"),
    ]])
    await update.message.reply_text(
        "Tiers:\n\n"
        "*Pro $49/mo* - real-time signals, watchlist, whale alerts\n"
        "*Whale $199/mo* - full whales list + PnL + copy-trade alerts\n"
        "*Signal $0.50* - one-shot deep report\n\n"
        "Payment via Telegram Stars (soon).",
        reply_markup=kb,
        parse_mode="Markdown",
    )


async def tier_picked(update, ctx):
    q = update.callback_query
    await q.answer()
    tier = q.data.split(":", 1)[1]
    if tier == "pro":
        await q.edit_message_text("Pro $49/mo - coming soon via Telegram Stars.")
    elif tier == "whale":
        await q.edit_message_text("Whale $199/mo - DM @guiboratto50 for early access.")
    else:
        await q.edit_message_text("Signal $0.50 - one-shot, payment via Stars soon.")


async def cmd_help(update, ctx):
    await update.message.reply_text(
        "/start - main menu\n"
        "/whales - top 20 whale wallets\n"
        "/signals - tokens bought by 3+ whales\n"
        "/track <TICKER> - add to watchlist (Pro)\n"
        "/analyze <TICKER> - one-shot advisor\n"
        "/tier - subscription\n"
        "/cancel - cancel dialog\n\n"
        "Data: Archam + DeBank. AI: OpenRouter."
    )


async def cmd_cancel(update, ctx):
    await update.message.reply_text("Cancelled. /start")
    return ConversationHandler.END


async def asset_picked(update, ctx):
    ctx.user_data["asset"] = update.message.text.strip().upper()
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("1d", callback_data="hor:1d"),
        InlineKeyboardButton("1w", callback_data="hor:1w"),
        InlineKeyboardButton("1m", callback_data="hor:1m"),
    ]])
    await update.message.reply_text(f"Horizon for {ctx.user_data['asset']}?", reply_markup=kb)
    return HORIZON


async def horizon_picked(update, ctx):
    q = update.callback_query
    await q.answer()
    ctx.user_data["horizon"] = q.data.split(":", 1)[1]
    await q.edit_message_text("Entry / budget ('market' or '$500'):")
    return ENTRY


async def entry_picked(update, ctx):
    ctx.user_data["entry"] = update.message.text.strip()
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("Low 1-3%", callback_data="risk:low"),
        InlineKeyboardButton("Med 3-7%", callback_data="risk:med"),
        InlineKeyboardButton("High 7-15%", callback_data="risk:high"),
    ]])
    await update.message.reply_text("Risk per trade?", reply_markup=kb)
    return RISK


async def risk_picked(update, ctx):
    q = update.callback_query
    await q.answer()
    ctx.user_data["risk"] = q.data.split(":", 1)[1]
    return await _analyze(update, ctx)


async def followup(update, ctx):
    user_id = update.effective_user.id
    if _limit_reached(user_id):
        return await _show_paywall(update, ctx)
    increment_usage(user_id)
    ctx.user_data.setdefault("followups", []).append(update.message.text)
    return await _analyze(update, ctx, is_followup=True)


def _limit_reached(user_id):
    user = get_user(user_id)
    if user and user["tier"] in ("pro", "whale", "signal"):
        return False
    today = datetime.utcnow().strftime("%Y-%m-%d")
    return user and user["usage_date"] == today and user["usage_count"] >= FREE_DAILY_LIMIT


async def _show_paywall(update, ctx):
    if update.message:
        await update.message.reply_text("Free limit reached. /tier")
    return ConversationHandler.END


async def _analyze(update, ctx, is_followup=False):
    user_id = update.effective_user.id
    d = ctx.user_data
    asset = d.get("asset", "?")
    msg = update.message or update.callback_query.message
    await msg.reply_text(f"Fetching data for {asset}...")
    cg = await coingecko(asset)
    ar = await md_archam(asset)
    payload = {
        "strategy": d.get("strategy"), "asset": asset,
        "horizon": d.get("horizon"), "entry": d.get("entry"),
        "risk": d.get("risk"), "coingecko": cg,
        "archam": ar, "followups": d.get("followups", []),
        "is_followup": is_followup,
    }
    advisor = Advisor()
    text = await advisor.analyze(payload)
    save_history(user_id, json.dumps(payload), text)
    await msg.reply_text(text)
    if not is_followup:
        await msg.reply_text("Need more detail? Just ask, or /cancel")
    return FOLLOWUPS