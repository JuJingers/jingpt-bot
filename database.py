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
            ("requests_reset_at",    "TIMESTAMP DEFAULT NULL"),
        ]:
            try:
                await db.execute(f"ALTER TABLE users ADD COLUMN {col} {definition}")
                await db.commit()
            except Exception:
                pass

        await db.execute("""
            CREATE TABLE IF NOT EXISTS chats (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                title      TEXT    DEFAULT 'Новый чат',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                chat_id    INTEGER DEFAULT NULL,
                role       TEXT    NOT NULL,
                content    TEXT    NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)
        # Миграция: добавляем chat_id в существующую таблицу
        try:
            await db.execute("ALTER TABLE messages ADD COLUMN chat_id INTEGER DEFAULT NULL")
            await db.commit()
        except Exception:
            pass
        # Создаём transactions (если нет)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         INTEGER NOT NULL,
                amount_rub      REAL    NOT NULL,
                requests_added  INTEGER DEFAULT 0,
                plan            TEXT    DEFAULT '',
                payment_id      TEXT    DEFAULT '',
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)
        await db.commit()
        # Миграции таблицы transactions (для старых баз на Railway)
        for col, definition in [
            ("plan",           "TEXT DEFAULT ''"),
            ("requests_added", "INTEGER DEFAULT 0"),
            ("payment_id",     "TEXT DEFAULT ''"),
        ]:
            try:
                await db.execute(f"ALTER TABLE transactions ADD COLUMN {col} {definition}")
                await db.commit()
            except Exception:
                pass


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
        await db.commit()
        # Логируем транзакцию (не критично если упадёт — подписка уже активирована)
        try:
            await db.execute(
                """INSERT INTO transactions (user_id, amount_rub, requests_added, plan, payment_id)
                   VALUES (?, ?, ?, ?, ?)""",
                (user_id, price, 0, plan, payment_id)
            )
            await db.commit()
        except Exception as e:
            print(f"⚠️ transactions insert error (non-critical): {e}")


# ── Чаты ─────────────────────────────────────────────────────────────────────

async def get_or_create_default_chat(user_id: int) -> dict:
    """Возвращает первый чат пользователя, создаёт если нет."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM chats WHERE user_id = ? ORDER BY id ASC LIMIT 1", (user_id,)
        ) as cur:
            chat = await cur.fetchone()

        if not chat:
            await db.execute(
                "INSERT INTO chats (user_id, title) VALUES (?, ?)",
                (user_id, "Основной чат")
            )
            await db.commit()
            async with db.execute(
                "SELECT * FROM chats WHERE user_id = ? ORDER BY id DESC LIMIT 1", (user_id,)
            ) as cur:
                chat = await cur.fetchone()
            # Привязываем старые сообщения без chat_id к этому чату
            await db.execute(
                "UPDATE messages SET chat_id = ? WHERE user_id = ? AND chat_id IS NULL",
                (chat["id"], user_id)
            )
            await db.commit()

        return dict(chat)


async def get_user_chats(user_id: int) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT c.*,
               (SELECT content FROM messages WHERE chat_id=c.id ORDER BY id DESC LIMIT 1) as last_msg,
               (SELECT created_at FROM messages WHERE chat_id=c.id ORDER BY id DESC LIMIT 1) as last_at
               FROM chats c WHERE c.user_id=? ORDER BY COALESCE(last_at, c.created_at) DESC""",
            (user_id,)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def create_chat(user_id: int, title: str = "Новый чат") -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute(
            "INSERT INTO chats (user_id, title) VALUES (?, ?)", (user_id, title)
        )
        await db.commit()
        async with db.execute(
            "SELECT * FROM chats WHERE user_id=? ORDER BY id DESC LIMIT 1", (user_id,)
        ) as cur:
            return dict(await cur.fetchone())


async def rename_chat(chat_id: int, user_id: int, title: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE chats SET title=? WHERE id=? AND user_id=?", (title, chat_id, user_id)
        )
        await db.commit()


async def delete_chat(chat_id: int, user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM messages WHERE chat_id=? AND user_id=?", (chat_id, user_id)
        )
        await db.execute(
            "DELETE FROM chats WHERE id=? AND user_id=?", (chat_id, user_id)
        )
        await db.commit()


async def save_message(user_id: int, role: str, content: str, chat_id: int | None = None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO messages (user_id, chat_id, role, content) VALUES (?, ?, ?, ?)",
            (user_id, chat_id, role, content),
        )
        await db.commit()


async def get_chat_history(user_id: int, limit: int = 20, chat_id: int | None = None) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if chat_id is not None:
            async with db.execute(
                """SELECT role, content, created_at FROM messages
                   WHERE user_id=? AND chat_id=? ORDER BY id DESC LIMIT ?""",
                (user_id, chat_id, limit),
            ) as cur:
                rows = await cur.fetchall()
        else:
            async with db.execute(
                """SELECT role, content, created_at FROM messages
                   WHERE user_id=? ORDER BY id DESC LIMIT ?""",
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
                           requests_added: int = 0, payment_id: str = ""):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO transactions (user_id, amount_rub, requests_added, plan, payment_id)
               VALUES (?, ?, ?, ?, ?)""",
            (user_id, amount_rub, requests_added, "", payment_id),
        )
        await db.commit()
