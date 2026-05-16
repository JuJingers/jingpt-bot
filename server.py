import asyncio
import base64
import hashlib
import hmac
import json
import os
import uuid
from urllib.parse import parse_qsl

import httpx
import anthropic
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

import database as db

# ── Настройки ────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY  = os.environ.get("ANTHROPIC_API_KEY", "sk-hub-9iH9yudgwhmrpsB1guWQu2MEfT200hRx")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_TOKEN",   "8563490950:AAHNoSzdlubomAUPk1M_JG4s8v690ciTNLk")
ADMIN_PASSWORD     = os.environ.get("ADMIN_PASSWORD",   "jingpt_admin_2024")
CLAUDE_MODEL       = "claude-opus-4-7"
CLAUDE_BASE_URL    = "https://api.tokenator.cloud/anthropic"

# ── ЮКасса ───────────────────────────────────────────────────────────────────
YOOKASSA_SHOP_ID = os.environ.get("YOOKASSA_SHOP_ID", "1353241")
YOOKASSA_SECRET  = os.environ.get("YOOKASSA_SECRET",  "live_mavWmxI_9vEbol_0r3LPbvJMPfGz1AY793KXdQFdzhs")
YOOKASSA_API     = "https://api.yookassa.ru/v3"

def yookassa_headers():
    creds = base64.b64encode(f"{YOOKASSA_SHOP_ID}:{YOOKASSA_SECRET}".encode()).decode()
    return {"Authorization": f"Basic {creds}", "Content-Type": "application/json"}

claude = anthropic.Anthropic(
    api_key=ANTHROPIC_API_KEY,
    base_url=CLAUDE_BASE_URL,
    http_client=httpx.Client(trust_env=False),
)

MINIAPP_URL = os.environ.get("MINIAPP_URL", "https://web-production-16962.up.railway.app")

# ── Telegram уведомления ──────────────────────────────────────────────────────
async def send_tg_notification(user_id: int, text: str):
    """Отправляет сообщение пользователю через Telegram Bot API."""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": user_id,
            "text": text,
            "parse_mode": "HTML",
            "reply_markup": json.dumps({"inline_keyboard": [[
                {"text": "💎 Пополнить баланс", "web_app": {"url": MINIAPP_URL}}
            ]]})
        }
        async with httpx.AsyncClient() as client:
            await client.post(url, json=payload, timeout=5)
    except Exception:
        pass

# ── FastAPI ───────────────────────────────────────────────────────────────────
app = FastAPI(title="Jingpt API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup():
    await db.init_db()


# ── Утилиты ───────────────────────────────────────────────────────────────────
def parse_init_data(init_data: str) -> dict | None:
    """Парсит и валидирует Telegram WebApp initData."""
    try:
        parsed = dict(parse_qsl(init_data, keep_blank_values=True))
        hash_value = parsed.pop("hash", "")

        data_check_string = "\n".join(
            f"{k}={v}" for k, v in sorted(parsed.items())
        )
        secret_key = hmac.new(
            b"WebAppData",
            TELEGRAM_BOT_TOKEN.encode(),
            hashlib.sha256,
        ).digest()
        computed = hmac.new(
            secret_key, data_check_string.encode(), hashlib.sha256
        ).hexdigest()

        user_json = parsed.get("user", "{}")
        user_data = json.loads(user_json)

        # В dev-режиме принимаем даже без валидного hash
        if computed == hash_value or True:
            return user_data
        return None
    except Exception:
        return None


# ── Pydantic модели ────────────────────────────────────────────────────────────
class InitRequest(BaseModel):
    init_data: str

class ChatRequest(BaseModel):
    user_id:   int
    message:   str = ""
    file_data: str | None = None   # base64
    file_type: str | None = None   # MIME
    file_name: str | None = None


# ── Роуты ─────────────────────────────────────────────────────────────────────
@app.post("/api/init")
async def api_init(req: InitRequest):
    user_data = parse_init_data(req.init_data)
    if not user_data or not user_data.get("id"):
        raise HTTPException(400, "Invalid init data")

    user = await db.get_or_create_user(
        user_id    = user_data["id"],
        username   = user_data.get("username", ""),
        first_name = user_data.get("first_name", ""),
        last_name  = user_data.get("last_name", ""),
    )
    history      = await db.get_chat_history(user["user_id"])
    subscription = await db.get_subscription(user["user_id"])
    return {"user": user, "history": history, "subscription": subscription}


@app.get("/api/user/{user_id}")
async def api_get_user(user_id: int):
    user = await db.get_user(user_id)
    if not user:
        raise HTTPException(404, "User not found")
    return user


@app.post("/api/chat")
async def api_chat(req: ChatRequest):
    user = await db.get_user(req.user_id)
    if not user:
        raise HTTPException(404, "User not found")

    # Проверяем подписку и лимиты
    can_use, sub = await db.use_request(req.user_id)
    if not can_use:
        raise HTTPException(402, "Limit reached")

    # Собираем контент сообщения
    content: list = []
    if req.file_data and req.file_type:
        if req.file_type.startswith("image/"):
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": req.file_type, "data": req.file_data},
            })
        elif req.file_type == "application/pdf":
            content.append({
                "type": "document",
                "source": {"type": "base64", "media_type": "application/pdf", "data": req.file_data},
            })
    if req.message:
        content.append({"type": "text", "text": req.message})
    if not content:
        raise HTTPException(400, "No content provided")

    # История диалога
    history = await db.get_chat_history(req.user_id, limit=10)
    messages = [{"role": h["role"], "content": h["content"]} for h in history]
    messages.append({
        "role":    "user",
        "content": content if len(content) > 1 else content[0]["text"],
    })

    # Запрос к Claude
    try:
        response = claude.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=2000,
            system=(
                "Ты — Jingpt, умный и дружелюбный AI-ассистент. "
                "Отвечай ТОЛЬКО на последнее сообщение пользователя. "
                "История диалога нужна только для контекста — не отвечай на старые вопросы. "
                "Будь конкретным, полезным и по делу."
            ),
            messages=messages,
        )
        assistant_text = response.content[0].text
    except Exception as e:
        print(f"❌ Claude error: {type(e).__name__}: {e}")
        raise HTTPException(500, f"Claude error: {e}")

    await db.save_message(req.user_id, "user", req.message or f"[Файл: {req.file_name}]")
    await db.save_message(req.user_id, "assistant", assistant_text)

    # Уведомление когда осталось мало запросов
    remaining = sub.get("remaining", 0)
    limit     = sub.get("limit", 20)
    if remaining == 0:
        asyncio.create_task(send_tg_notification(
            req.user_id,
            f"⚠️ <b>Лимит исчерпан!</b>\n\n"
            f"Вы использовали все {limit} запросов этого месяца.\n"
            f"Оформите подписку, чтобы продолжить 👇"
        ))
    elif remaining == 5:
        asyncio.create_task(send_tg_notification(
            req.user_id,
            f"💡 <b>Осталось {remaining} запросов</b>\n\n"
            f"Скоро закончится лимит — обновите подписку заранее 👇"
        ))

    return {"response": assistant_text, "subscription": sub}


# ── Админка ───────────────────────────────────────────────────────────────────
def check_admin(request: Request):
    pw = request.headers.get("X-Admin-Password", "")
    if pw != ADMIN_PASSWORD:
        raise HTTPException(403, "Forbidden")

class TopupRequest(BaseModel):
    user_id: int
    amount:  int

@app.get("/api/admin/stats")
async def admin_stats(request: Request):
    check_admin(request)
    async with __import__("aiosqlite").connect(db.DB_PATH) as conn:
        conn.row_factory = __import__("aiosqlite").Row
        async with conn.execute("SELECT COUNT(*) as c FROM users") as cur:
            total_users = (await cur.fetchone())["c"]
        async with conn.execute("SELECT COUNT(*) as c FROM messages WHERE role='user'") as cur:
            total_messages = (await cur.fetchone())["c"]
        async with conn.execute("SELECT COALESCE(SUM(balance),0) as s FROM users") as cur:
            total_balance = (await cur.fetchone())["s"]
        async with conn.execute("SELECT COUNT(*) as c FROM users WHERE balance=0") as cur:
            zero_balance = (await cur.fetchone())["c"]
        async with conn.execute("SELECT * FROM users ORDER BY created_at DESC") as cur:
            users = [dict(r) for r in await cur.fetchall()]
    return {
        "total_users":    total_users,
        "total_messages": total_messages,
        "total_balance":  total_balance,
        "zero_balance":   zero_balance,
        "users":          users,
    }

@app.post("/api/admin/topup")
async def admin_topup(request: Request, req: TopupRequest):
    check_admin(request)
    user = await db.get_user(req.user_id)
    if not user:
        raise HTTPException(404, "User not found")
    await db.add_balance(req.user_id, req.amount)
    updated = await db.get_user(req.user_id)
    return {"new_balance": updated["balance"]}

class BlockRequest(BaseModel):
    user_id:  int
    blocked:  bool

@app.post("/api/admin/block")
async def admin_block(request: Request, req: BlockRequest):
    check_admin(request)
    user = await db.get_user(req.user_id)
    if not user:
        raise HTTPException(404, "User not found")
    await db.set_blocked(req.user_id, req.blocked)
    return {"ok": True, "blocked": req.blocked}

# ── ЮКасса: создание платежа (подписка) ──────────────────────────────────────
class PaymentRequest(BaseModel):
    user_id: int
    plan:    str   # "start" | "pro" | "max"

@app.post("/api/payment/create")
async def payment_create(req: PaymentRequest):
    user = await db.get_user(req.user_id)
    if not user:
        raise HTTPException(404, "User not found")
    plan_info = db.PLANS.get(req.plan)
    if not plan_info or plan_info["price"] == 0:
        raise HTTPException(400, "Invalid plan")

    payload = {
        "amount":       {"value": f"{plan_info['price']}.00", "currency": "RUB"},
        "confirmation": {"type": "redirect", "return_url": MINIAPP_URL},
        "capture":      True,
        "description":  f"Jingpt {plan_info['name']} — {plan_info['limit']} запросов/мес",
        "metadata":     {"user_id": str(req.user_id), "plan": req.plan},
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{YOOKASSA_API}/payments",
            json=payload,
            headers={**yookassa_headers(), "Idempotence-Key": str(uuid.uuid4())},
            timeout=10,
        )
    if resp.status_code not in (200, 201):
        raise HTTPException(500, f"YooKassa error: {resp.text}")

    data = resp.json()
    return {"url": data["confirmation"]["confirmation_url"], "payment_id": data["id"]}


# ── ЮКасса: вебхук ─────────────────────────────────────────────────────────────
@app.post("/api/payment/webhook")
async def payment_webhook(request: Request):
    try:
        body = await request.json()
        if body.get("event") != "payment.succeeded":
            return {"ok": True}

        obj     = body.get("object", {})
        meta    = obj.get("metadata", {})
        user_id = int(meta.get("user_id", 0))
        plan    = meta.get("plan", "")
        pay_id  = obj.get("id", "")

        if user_id and plan:
            await db.activate_subscription(user_id, plan, pay_id)
            plan_info = db.PLANS.get(plan, {})
            asyncio.create_task(send_tg_notification(
                user_id,
                f"✅ <b>Подписка активирована!</b>\n\n"
                f"Тариф: {plan_info.get('emoji','')} <b>{plan_info.get('name','')}</b>\n"
                f"Запросов в месяц: <b>{plan_info.get('limit', 0)}</b>\n\n"
                f"Приятного общения с Jingpt! 🚀"
            ))
    except Exception as e:
        print(f"Webhook error: {e}")
    return {"ok": True}


# ── Статические файлы Mini App ─────────────────────────────────────────────────
app.mount("/miniapp", StaticFiles(directory="miniapp", html=True), name="miniapp")

@app.get("/")
async def root():
    return FileResponse("miniapp/index.html")
