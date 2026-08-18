"""SQLite persistence - users, watchlist, trades, journal, alerts."""
import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "gio.db"


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    with _conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            tier TEXT NOT NULL DEFAULT 'free',
            usage_date TEXT,
            usage_count INTEGER DEFAULT 0,
            lang TEXT DEFAULT 'en',
            created_at INTEGER DEFAULT (strftime('%s','now'))
        );

        CREATE TABLE IF NOT EXISTS watchlist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            chain TEXT DEFAULT 'eth',
            alert_on_buy INTEGER DEFAULT 1,
            added_at INTEGER DEFAULT (strftime('%s','now')),
            UNIQUE(user_id, symbol, chain)
        );

        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,           -- 'buy' / 'sell'
            entry_price REAL NOT NULL,
            size REAL NOT NULL,           -- units (coins) or USD notional
            stop_loss REAL,
            take_profit REAL,
            status TEXT DEFAULT 'open',   -- 'open' / 'closed'
            exit_price REAL,
            closed_at INTEGER,
            opened_at INTEGER DEFAULT (strftime('%s','now')),
            notes TEXT
        );

        CREATE TABLE IF NOT EXISTS journal (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            title TEXT,
            body TEXT,
            created_at INTEGER DEFAULT (strftime('%s','now'))
        );

        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            direction TEXT,               -- 'buy' / 'sell' / 'any'
            active INTEGER DEFAULT 1,
            created_at INTEGER DEFAULT (strftime('%s','now'))
        );
        """)


# ========== users ==========

def get_user(user_id: int):
    with _conn() as c:
        cur = c.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        if row:
            return dict(row)
        c.execute("INSERT INTO users(user_id) VALUES(?)", (user_id,))
        c.commit()
        return get_user(user_id)


def set_user_tier(user_id: int, tier: str):
    with _conn() as c:
        c.execute("UPDATE users SET tier=? WHERE user_id=?", (tier, user_id))
        c.commit()


def increment_usage(user_id: int):
    today = time.strftime("%Y-%m-%d")
    with _conn() as c:
        u = get_user(user_id)
        if u["usage_date"] != today:
            c.execute("UPDATE users SET usage_date=?, usage_count=1 WHERE user_id=?",
                      (today, user_id))
        else:
            c.execute("UPDATE users SET usage_count=usage_count+1 WHERE user_id=?",
                      (user_id,))
        c.commit()


# ========== watchlist ==========

def add_watch(user_id: int, symbol: str, chain: str = "eth") -> bool:
    try:
        with _conn() as c:
            c.execute(
                "INSERT INTO watchlist(user_id, symbol, chain) VALUES(?,?,?)",
                (user_id, symbol.upper(), chain))
            c.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def remove_watch(user_id: int, symbol: str) -> bool:
    with _conn() as c:
        cur = c.execute(
            "DELETE FROM watchlist WHERE user_id=? AND symbol=?",
            (user_id, symbol.upper()))
        c.commit()
        return cur.rowcount > 0


def list_watch(user_id: int) -> list:
    with _conn() as c:
        rows = c.execute(
            "SELECT symbol, chain, added_at FROM watchlist WHERE user_id=? ORDER BY added_at DESC",
            (user_id,)).fetchall()
        return [dict(r) for r in rows]


def all_watchers_for(symbol: str, chain: str = "eth") -> list:
    """For alert dispatcher: who has this token in watchlist."""
    with _conn() as c:
        rows = c.execute(
            "SELECT DISTINCT user_id FROM watchlist WHERE symbol=? AND chain=? AND alert_on_buy=1",
            (symbol.upper(), chain)).fetchall()
        return [r["user_id"] for r in rows]


# ========== trades ==========

def open_trade(user_id: int, symbol: str, side: str, entry_price: float,
               size: float, stop_loss: float = None, take_profit: float = None,
               notes: str = None) -> int:
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO trades(user_id, symbol, side, entry_price, size, stop_loss, take_profit, notes) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (user_id, symbol.upper(), side.lower(), entry_price, size,
             stop_loss, take_profit, notes))
        c.commit()
        return cur.lastrowid


def close_trade(trade_id: int, user_id: int, exit_price: float) -> bool:
    with _conn() as c:
        cur = c.execute(
            "UPDATE trades SET status='closed', exit_price=?, closed_at=strftime('%s','now') "
            "WHERE id=? AND user_id=? AND status='open'",
            (exit_price, trade_id, user_id))
        c.commit()
        return cur.rowcount > 0


def list_trades(user_id: int, status: str = None) -> list:
    with _conn() as c:
        if status:
            rows = c.execute(
                "SELECT * FROM trades WHERE user_id=? AND status=? ORDER BY opened_at DESC",
                (user_id, status)).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM trades WHERE user_id=? ORDER BY opened_at DESC",
                (user_id,)).fetchall()
        return [dict(r) for r in rows]


def portfolio_pnl(user_id: int, prices: dict) -> dict:
    """Compute unrealized + realized PnL given current prices map {symbol: price}."""
    open_t = [t for t in list_trades(user_id, "open")]
    closed_t = [t for t in list_trades(user_id, "closed")]
    unrealized = 0.0
    for t in open_t:
        cur = prices.get(t["symbol"])
        if cur is None:
            continue
        if t["side"] == "buy":
            unrealized += (cur - t["entry_price"]) * t["size"]
        else:
            unrealized += (t["entry_price"] - cur) * t["size"]
    realized = 0.0
    for t in closed_t:
        if t["side"] == "buy":
            realized += (t["exit_price"] - t["entry_price"]) * t["size"]
        else:
            realized += (t["entry_price"] - t["exit_price"]) * t["size"]
    return {"unrealized": unrealized, "realized": realized,
            "n_open": len(open_t), "n_closed": len(closed_t)}


# ========== journal ==========

def add_journal(user_id: int, symbol: str, title: str, body: str) -> int:
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO journal(user_id, symbol, title, body) VALUES(?,?,?,?)",
            (user_id, symbol.upper(), title, body))
        c.commit()
        return cur.lastrowid


def list_journal(user_id: int, symbol: str = None, limit: int = 20) -> list:
    with _conn() as c:
        if symbol:
            rows = c.execute(
                "SELECT * FROM journal WHERE user_id=? AND symbol=? ORDER BY created_at DESC LIMIT ?",
                (user_id, symbol.upper(), limit)).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM journal WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
                (user_id, limit)).fetchall()
        return [dict(r) for r in rows]