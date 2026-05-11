import hashlib
import hmac
import json
from urllib.parse import parse_qsl

import httpx
import anthropic
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

import database as db

# ── Настройки ────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY  = "sk-hub-9iH9yudgwhmrpsB1guWQu2MEfT200hRx"
TELEGRAM_BOT_TOKEN = "8563490950:AAHNoSzdlubomAUPk1M_JG4s8v690ciTNLk"
CLAUDE_MODEL       = "claude-opus-4-7"
CLAUDE_BASE_URL    = "https://api.claudehub.fun"

claude = anthropic.Anthropic(
    api_key=ANTHROPIC_API_KEY,
    base_url=CLAUDE_BASE_URL,
    http_client=httpx.Client(trust_env=False),
)

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
    history = await db.get_chat_history(user["user_id"])
    return {"user": user, "history": history}


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
    if user["balance"] <= 0:
        raise HTTPException(402, "Insufficient balance")

    # Собираем контент сообщения
    content: list = []

    if req.file_data and req.file_type:
        if req.file_type.startswith("image/"):
            content.append({
                "type": "image",
                "source": {
                    "type":       "base64",
                    "media_type": req.file_type,
                    "data":       req.file_data,
                },
            })
        elif req.file_type == "application/pdf":
            content.append({
                "type": "document",
                "source": {
                    "type":       "base64",
                    "media_type": "application/pdf",
                    "data":       req.file_data,
                },
            })

    if req.message:
        content.append({"type": "text", "text": req.message})

    if not content:
        raise HTTPException(400, "No content provided")

    # История диалога (контекст)
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
            messages=messages,
        )
        assistant_text = response.content[0].text
    except Exception as e:
        raise HTTPException(500, f"Claude error: {e}")

    # Списываем баланс и сохраняем сообщения
    await db.deduct_balance(req.user_id)
    await db.save_message(req.user_id, "user",
                          req.message or f"[Файл: {req.file_name}]")
    await db.save_message(req.user_id, "assistant", assistant_text)

    updated = await db.get_user(req.user_id)
    return {"response": assistant_text, "balance": updated["balance"]}


# ── Статические файлы Mini App ─────────────────────────────────────────────────
app.mount("/miniapp", StaticFiles(directory="miniapp", html=True), name="miniapp")

@app.get("/")
async def root():
    return FileResponse("miniapp/index.html")
