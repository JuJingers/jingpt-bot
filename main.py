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
    base_url="https://api.claudehub.fun",
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
    name = message.from_user.first_name or "друг"
    await message.answer(
        f"👋 Привет, <b>{name}</b>!\n\n"
        f"Я <b>Jingpt</b> — AI-ассистент на базе модели <b>Claude Opus 4.7</b>.\n\n"
        f"Могу помочь с:\n"
        f"• 💡 Любыми вопросами и идеями\n"
        f"• 📄 Анализом документов и файлов\n"
        f"• ✍️ Текстами, кодом, переводами\n"
        f"• 🧠 Решением сложных задач\n\n"
        f"💎 Твой баланс: <b>{user['balance']} запросов</b>\n\n"
        f"Открой приложение и начни общение:",
        parse_mode="HTML",
        reply_markup=main_keyboard(),
    )


# ── /balance ──────────────────────────────────────────────────────────────────
@dp.message(Command("balance"))
async def cmd_balance(message: Message):
    user = await db.get_or_create_user(
        user_id    = message.from_user.id,
        username   = message.from_user.username   or "",
        first_name = message.from_user.first_name or "",
        last_name  = message.from_user.last_name  or "",
    )
    balance = user["balance"]
    if balance == 0:
        text = "💔 Баланс пуст. Пополните, чтобы продолжить общение."
    elif balance <= 3:
        text = f"⚠️ Баланс: <b>{balance} запр.</b> — заканчивается, пополните заранее."
    else:
        text = f"💎 Баланс: <b>{balance} запросов</b>"

    await message.answer(text, parse_mode="HTML", reply_markup=buy_keyboard())


# ── /help ─────────────────────────────────────────────────────────────────────
@dp.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "📖 <b>Как пользоваться Jingpt</b>\n\n"
        "<b>Команды:</b>\n"
        "/start — главное меню\n"
        "/balance — проверить баланс\n"
        "/help — эта справка\n\n"
        "<b>Возможности:</b>\n"
        "• Просто пиши сообщение — отвечу\n"
        "• Прикрепляй файлы через приложение\n"
        "• Бот помнит контекст диалога\n\n"
        "<b>Тарифы:</b>\n"
        "• 1 запрос — 15 ₽\n"
        "• 10 запросов — 99 ₽\n"
        "• 50 запросов — 349 ₽\n\n"
        "По вопросам: @DadaYaKiruha",
        parse_mode="HTML",
        reply_markup=main_keyboard(),
    )


# ── Текстовые сообщения ───────────────────────────────────────────────────────
@dp.message(F.text)
async def handle_text(message: Message):
    await db.get_or_create_user(
        user_id    = message.from_user.id,
        username   = message.from_user.username   or "",
        first_name = message.from_user.first_name or "",
        last_name  = message.from_user.last_name  or "",
    )
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
