"""SQLite persistence."""
import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "gio.db"


def init_db():
    with sqlite3.connect(DB_PATH) as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            tier TEXT NOT NULL DEFAULT 'free',
            usage_date TEXT,
            usage_count INTEGER DEFAULT 0,
            created_at INTEGER DEFAULT (strftime('%s','now'))
        );
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            context TEXT,
            response TEXT,
            ts INTEGER DEFAULT (strftime('%s','now'))
        );
        """)


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


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
            c.execute(
                "UPDATE users SET usage_date=?, usage_count=1 WHERE user_id=?",
                (today, user_id),
            )
        else:
            c.execute(
                "UPDATE users SET usage_count=usage_count+1 WHERE user_id=?",
                (user_id,),
            )
        c.commit()


def save_history(user_id: int, context: str, response: str):
    with _conn() as c:
        c.execute(
            "INSERT INTO history(user_id, context, response) VALUES(?,?,?)",
            (user_id, context[:5000], response[:5000]),
        )
        c.commit()