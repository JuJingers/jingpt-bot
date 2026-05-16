import asyncio
import os
import httpx
import uvicorn
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from aiogram.filters import Command, CommandStart
from aiogram.client.session.aiohttp import AiohttpSession
import anthropic

import database as db
from server import app

# ── Настройки ─────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN    = os.environ.get("TELEGRAM_TOKEN", "8563490950:AAHNoSzdlubomAUPk1M_JG4s8v690ciTNLk")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "sk-hub-9iH9yudgwhmrpsB1guWQu2MEfT200hRx")
MINIAPP_URL       = os.environ.get("MINIAPP_URL", "https://example.up.railway.app")
USE_PROXY         = os.environ.get("USE_PROXY", "false").lower() == "true"
PORT              = int(os.environ.get("PORT", 8000))

# ── Telegram бот ───────────────────────────────────────────────────────────────
session = AiohttpSession(proxy="socks4://127.0.0.1:10808") if USE_PROXY else AiohttpSession()
bot = Bot(token=TELEGRAM_TOKEN, session=session)
dp  = Dispatcher()

claude = anthropic.Anthropic(
    api_key=ANTHROPIC_API_KEY,
    base_url="https://api.tokenator.cloud/anthropic",
    http_client=httpx.Client(trust_env=False),
)


# ── Клавиатуры ────────────────────────────────────────────────────────────────
def main_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🚀 Открыть Jingpt", web_app=WebAppInfo(url=MINIAPP_URL))
    ]])

def buy_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="💎 Пополнить баланс", web_app=WebAppInfo(url=MINIAPP_URL))
    ]])


# ── /start ────────────────────────────────────────────────────────────────────
@dp.message(CommandStart())
async def cmd_start(message: Message):
    user = await db.get_or_create_user(
        user_id    = message.from_user.id,
        username   = message.from_user.username   or "",
        first_name = message.from_user.first_name or "",
        last_name  = message.from_user.last_name  or "",
    )
    if user.get("is_blocked"):
        await message.answer("🚫 Ваш аккаунт заблокирован.")
        return

    name = message.from_user.first_name or "друг"
    await message.answer(
        f"👋 Привет, <b>{name}</b>!\n\n"
        f"Я <b>Jingpt</b> — твой персональный AI-ассистент на базе <b>Claude Opus 4.7</b>.\n\n"
        f"━━━━━━━━━━━━━━━\n"
        f"💡 Любые вопросы и идеи\n"
        f"📄 Анализ документов и файлов\n"
        f"✍️ Тексты, код, переводы\n"
        f"🧠 Решение сложных задач\n"
        f"━━━━━━━━━━━━━━━\n\n"
        f"🆓 Тариф: <b>Бесплатно</b> — 10 запросов в месяц\n\n"
        f"📋 <b>Команды:</b>\n"
        f"/plan — моя подписка\n"
        f"/help — тарифы и помощь\n\n"
        f"Нажми кнопку ниже и начни общение 👇",
        parse_mode="HTML",
        reply_markup=main_keyboard(),
    )


# ── /plan ─────────────────────────────────────────────────────────────────────
@dp.message(Command("plan"))
@dp.message(Command("balance"))
async def cmd_plan(message: Message):
    user = await db.get_or_create_user(
        user_id    = message.from_user.id,
        username   = message.from_user.username   or "",
        first_name = message.from_user.first_name or "",
        last_name  = message.from_user.last_name  or "",
    )
    sub = await db.get_subscription(message.from_user.id)
    plan_emoji = sub.get("plan_emoji", "🆓")
    plan_name  = sub.get("plan_name",  "Бесплатно")
    used       = sub.get("used",       0)
    limit      = sub.get("limit",      10)
    remaining  = sub.get("remaining",  0)
    expires    = sub.get("expires")

    limit_str = "∞" if limit >= 999999 else str(limit)
    rem_str   = "∞" if remaining >= 999999 else str(remaining)

    text = (
        f"{plan_emoji} <b>Тариф: {plan_name}</b>\n\n"
        f"📊 Использовано: <b>{used} / {limit_str}</b> запросов\n"
        f"✅ Осталось: <b>{rem_str}</b>\n"
    )
    if expires and sub.get("plan") != "free":
        from datetime import datetime
        try:
            d = datetime.fromisoformat(expires)
            text += f"📅 Действует до: <b>{d.strftime('%d.%m.%Y')}</b>\n"
        except Exception:
            pass

    if remaining == 0:
        text += "\n⚠️ Лимит исчерпан — оформите подписку в приложении!"
    elif remaining <= 3:
        text += f"\n⚠️ Осталось мало запросов — обновите подписку заранее!"

    await message.answer(text, parse_mode="HTML", reply_markup=buy_keyboard())


# ── /help ─────────────────────────────────────────────────────────────────────
@dp.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "📖 <b>Как пользоваться Jingpt</b>\n\n"
        "<b>Команды:</b>\n"
        "/start — главное меню\n"
        "/plan — моя подписка и лимиты\n"
        "/help — эта справка\n\n"
        "<b>Возможности:</b>\n"
        "• Общение с AI через приложение\n"
        "• Прикрепляй файлы и фото\n"
        "• Бот помнит контекст диалога\n\n"
        "<b>Тарифы (в месяц):</b>\n"
        "🆓 Бесплатно — 10 запросов\n"
        "⚡ Старт — 200 запросов / 199 ₽\n"
        "🚀 Про — 1000 запросов / 499 ₽\n"
        "💎 Безлимит — ∞ запросов / 999 ₽\n\n"
        "По вопросам: @DadaYaKiruha",
        parse_mode="HTML",
        reply_markup=main_keyboard(),
    )


# ── Проверка блокировки (middleware) ─────────────────────────────────────────
async def is_blocked(user_id: int) -> bool:
    user = await db.get_user(user_id)
    return bool(user and user.get("is_blocked"))


# ── Текстовые сообщения ───────────────────────────────────────────────────────
@dp.message(F.text)
async def handle_text(message: Message):
    user = await db.get_or_create_user(
        user_id    = message.from_user.id,
        username   = message.from_user.username   or "",
        first_name = message.from_user.first_name or "",
        last_name  = message.from_user.last_name  or "",
    )
    if user.get("is_blocked"):
        await message.answer("🚫 Ваш аккаунт заблокирован.")
        return
    await message.answer(
        "💬 Общение с Jingpt доступно в приложении.\n"
        "Открой его и задай свой вопрос там:",
        reply_markup=main_keyboard(),
    )


# ── Запуск ────────────────────────────────────────────────────────────────────
async def run_bot():
    await db.init_db()
    print("✅ Бот запущен!")
    await dp.start_polling(bot)

async def run_server():
    config = uvicorn.Config(app, host="0.0.0.0", port=PORT, log_level="info")
    server = uvicorn.Server(config)
    print(f"✅ Сервер запущен на порту {PORT}")
    await server.serve()

async def main():
    await asyncio.gather(run_server(), run_bot())

if __name__ == "__main__":
    asyncio.run(main())
