"""GIO Trading Bot - Telegram advisor."""
import os
import json
import sqlite3
import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ConversationHandler, filters, ContextTypes,
)
from .advisor import Advisor
from .market_data import coingecko, debank, archam
from .db import init_db, get_user, set_user_tier, increment_usage, save_history

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("gio")

STRATEGY, ASSET, HORIZON, ENTRY, RISK, FOLLOWUPS = range(6)
FREE_DAILY_LIMIT = 5


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
    app.add_handler(CommandHandler("tier", cmd_tier))
    app.add_handler(CallbackQueryHandler(tier_picked, pattern="^tier:"))
    app.add_handler(CommandHandler("help", cmd_help))
    log.info("Bot starting...")
    app.run_polling(drop_pending_updates=True)


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("Intraday", callback_data="strat:intraday"),
        InlineKeyboardButton("Long-term", callback_data="strat:long"),
        InlineKeyboardButton("Swing", callback_data="strat:swing"),
    ]])
    await update.message.reply_text(
        "Privit! Ya GIO Trading Advisor.\nOberite strategiyu:",
        reply_markup=kb,
    )
    return STRATEGY


async def strategy_picked(update, ctx):
    q = update.callback_query
    await q.answer()
    ctx.user_data["strategy"] = q.data.split(":", 1)[1]
    await q.edit_message_text("Vvedit tiker abo nazvu aktyvu (BTC, ETH, AAPL, EURUSD):")
    return ASSET


async def asset_picked(update, ctx):
    ctx.user_data["asset"] = update.message.text.strip().upper()
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("1d", callback_data="hor:1d"),
        InlineKeyboardButton("1w", callback_data="hor:1w"),
        InlineKeyboardButton("1m", callback_data="hor:1m"),
        InlineKeyboardButton("3m+", callback_data="hor:3m"),
    ]])
    await update.message.reply_text(f"Goryzont dlya {ctx.user_data['asset']}?", reply_markup=kb)
    return HORIZON


async def horizon_picked(update, ctx):
    q = update.callback_query
    await q.answer()
    ctx.user_data["horizon"] = q.data.split(":", 1)[1]
    await q.edit_message_text("Tochka vhodu / byudzhet ('market' abo '$500'):")
    return ENTRY


async def entry_picked(update, ctx):
    ctx.user_data["entry"] = update.message.text.strip()
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("Low 1-3%", callback_data="risk:low"),
        InlineKeyboardButton("Med 3-7%", callback_data="risk:med"),
        InlineKeyboardButton("High 7-15%", callback_data="risk:high"),
    ]])
    await update.message.reply_text("Dopustymyj ryzyk na ugodu?", reply_markup=kb)
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


async def cmd_cancel(update, ctx):
    await update.message.reply_text("Cancelled. /start to begin again.")
    return ConversationHandler.END


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
        InlineKeyboardButton("Pro $9/mo", callback_data="tier:pro"),
        InlineKeyboardButton("Signal $0.50", callback_data="tier:signal"),
    ]])
    await update.message.reply_text(
        "Free plan: 5 analyses/day.\n\n"
        "Pro - limitless, watchlists, alerts.\n"
        "Signal - one-shot deep report.\n\n"
        "Payment via Telegram Stars (soon).",
        reply_markup=kb,
    )


async def tier_picked(update, ctx):
    q = update.callback_query
    await q.answer()
    tier = q.data.split(":", 1)[1]
    if tier == "pro":
        await q.edit_message_text("Pro - coming soon. DM @guiboratto50 for early access.")
    else:
        await q.edit_message_text("Signal - payment via Telegram Stars coming soon.")


async def cmd_help(update, ctx):
    await update.message.reply_text(
        "/start - dialog\n"
        "/analyze <TICKER> - quick analysis\n"
        "/tier - subscription\n"
        "/cancel - cancel dialog\n\n"
        "Data: CoinGecko, DeBank, Archam. AI: OpenRouter."
    )


def _limit_reached(user_id):
    user = get_user(user_id)
    if user and user["tier"] in ("pro", "signal"):
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
    db = await debank(asset)
    ar = await archam(asset)
    payload = {
        "strategy": d.get("strategy"), "asset": asset,
        "horizon": d.get("horizon"), "entry": d.get("entry"),
        "risk": d.get("risk"), "coingecko": cg, "debank": db,
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