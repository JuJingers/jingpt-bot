import os
import aiosqlite
from datetime import datetime, timedelta

DB_PATH = os.environ.get("DB_PATH", "jingpt.db")

# ── Тарифы ───────────────────────────────────────────────────────────────────
PLANS = {
    "free":  {"name": "Бесплатно", "limit": 10,     "price": 0,   "emoji": "🆓"},
    "start": {"name": "Старт",     "limit": 200,    "price": 199, "emoji": "⚡"},
    "pro":   {"name": "Про",       "limit": 1000,   "price": 499, "emoji": "🚀"},
    "max":   {"name": "Безлимит",  "limit": 999999, "price": 999, "emoji": "💎"},
}


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id              INTEGER PRIMARY KEY,
                username             TEXT      DEFAULT '',
                first_name           TEXT      DEFAULT '',
                last_name            TEXT      DEFAULT '',
                balance              INTEGER   DEFAULT 0,
                is_blocked           INTEGER   DEFAULT 0,
                subscription_type    TEXT      DEFAULT 'free',
                subscription_expires TIMESTAMP DEFAULT NULL,
                requests_used        INTEGER   DEFAULT 0,
                requests_reset_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Миграция существующих баз
        for col, definition in [
            ("is_blocked",           "INTEGER DEFAULT 0"),
            ("subscription_type",    "TEXT DEFAULT 'free'"),
            ("subscription_expires", "TIMESTAMP DEFAULT NULL"),
            ("requests_used",        "INTEGER DEFAULT 0"),
            ("requests_reset_at",    "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"),
        ]:
            try:
                await db.execute(f"ALTER TABLE users ADD COLUMN {col} {definition}")
                await db.commit()
            except Exception:
                pass

        await db.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                role       TEXT    NOT NULL,
                content    TEXT    NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         INTEGER NOT NULL,
                amount_rub      REAL    NOT NULL,
                plan            TEXT    DEFAULT '',
                payment_id      TEXT    DEFAULT '',
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)
        await db.commit()


async def get_or_create_user(user_id: int, username: str = "",
                              first_name: str = "", last_name: str = "") -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ) as cur:
            user = await cur.fetchone()

        if not user:
            await db.execute(
                "INSERT INTO users (user_id, username, first_name, last_name) VALUES (?, ?, ?, ?)",
                (user_id, username, first_name, last_name),
            )
            await db.commit()
            async with db.execute(
                "SELECT * FROM users WHERE user_id = ?", (user_id,)
            ) as cur:
                user = await cur.fetchone()

        return dict(user)


async def get_user(user_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None


async def get_subscription(user_id: int) -> dict:
    """Возвращает актуальную информацию о подписке пользователя."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cur:
            user = await cur.fetchone()
        if not user:
            return {}
        user = dict(user)

    plan      = user.get("subscription_type", "free") or "free"
    expires   = user.get("subscription_expires")
    used      = user.get("requests_used", 0)
    reset_at  = user.get("requests_reset_at")
    now       = datetime.utcnow()

    # Проверяем истечение подписки
    if plan != "free" and expires:
        try:
            exp_dt = datetime.fromisoformat(str(expires))
            if now > exp_dt:
                plan = "free"
                # Сбрасываем подписку в базе
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute(
                        "UPDATE users SET subscription_type='free', subscription_expires=NULL WHERE user_id=?",
                        (user_id,)
                    )
                    await db.commit()
                expires = None
        except Exception:
            pass

    # Проверяем сброс месячного счётчика (каждые 30 дней)
    if reset_at:
        try:
            reset_dt = datetime.fromisoformat(str(reset_at))
            if (now - reset_dt).days >= 30:
                used = 0
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute(
                        "UPDATE users SET requests_used=0, requests_reset_at=? WHERE user_id=?",
                        (now.isoformat(), user_id)
                    )
                    await db.commit()
        except Exception:
            pass

    plan_info = PLANS.get(plan, PLANS["free"])
    limit     = plan_info["limit"]

    return {
        "plan":      plan,
        "plan_name": plan_info["name"],
        "plan_emoji":plan_info["emoji"],
        "limit":     limit,
        "used":      used,
        "remaining": max(0, limit - used),
        "expires":   str(expires) if expires else None,
    }


async def use_request(user_id: int) -> tuple[bool, dict]:
    """Использует один запрос. Возвращает (разрешено, subscription_info)."""
    sub = await get_subscription(user_id)
    if not sub:
        return False, {}

    if sub["remaining"] <= 0:
        return False, sub

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET requests_used = requests_used + 1 WHERE user_id = ?",
            (user_id,)
        )
        await db.commit()

    sub["used"]      += 1
    sub["remaining"] -= 1
    return True, sub


async def activate_subscription(user_id: int, plan: str, payment_id: str = ""):
    """Активирует подписку на 30 дней и сбрасывает счётчик запросов."""
    if plan not in PLANS:
        return
    now     = datetime.utcnow()
    expires = now + timedelta(days=30)
    price   = PLANS[plan]["price"]

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """UPDATE users
               SET subscription_type=?, subscription_expires=?,
                   requests_used=0, requests_reset_at=?
               WHERE user_id=?""",
            (plan, expires.isoformat(), now.isoformat(), user_id)
        )
        await db.execute(
            """INSERT INTO transactions (user_id, amount_rub, plan, payment_id)
               VALUES (?, ?, ?, ?)""",
            (user_id, price, plan, payment_id)
        )
        await db.commit()


async def save_message(user_id: int, role: str, content: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO messages (user_id, role, content) VALUES (?, ?, ?)",
            (user_id, role, content),
        )
        await db.commit()


async def get_chat_history(user_id: int, limit: int = 20) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT role, content, created_at FROM messages
               WHERE user_id = ? ORDER BY created_at DESC LIMIT ?""",
            (user_id, limit),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in reversed(rows)]


async def set_blocked(user_id: int, blocked: bool):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET is_blocked = ? WHERE user_id = ?",
            (1 if blocked else 0, user_id)
        )
        await db.commit()


# Оставляем для совместимости с админкой
async def add_balance(user_id: int, amount: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id)
        )
        await db.commit()


async def add_transaction(user_id: int, amount_rub: float,
                           requests_added: int, payment_id: str = ""):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO transactions (user_id, amount_rub, plan, payment_id)
               VALUES (?, ?, ?, ?)""",
            (user_id, amount_rub, "", payment_id),
        )
        await db.commit()
