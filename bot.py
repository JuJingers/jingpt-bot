import asyncio
import httpx
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from aiogram.filters import CommandStart
from aiogram.client.session.aiohttp import AiohttpSession
import anthropic

import database as db

TELEGRAM_TOKEN     = "8563490950:AAHNoSzdlubomAUPk1M_JG4s8v690ciTNLk"
ANTHROPIC_API_KEY  = "sk-hub-9iH9yudgwhmrpsB1guWQu2MEfT200hRx"
MINIAPP_URL        = "https://ТВОЙ_ДОМЕН"   # ← заменишь после деплоя

# Telegram через SOCKS прокси
session = AiohttpSession(proxy="socks4://127.0.0.1:10808")
bot = Bot(token=TELEGRAM_TOKEN, session=session)
dp  = Dispatcher()

# Anthropic без прокси
claude = anthropic.Anthropic(
    api_key=ANTHROPIC_API_KEY,
    base_url="https://api.claudehub.fun",
    http_client=httpx.Client(trust_env=False),
)


def main_keyboard():
    """Кнопка открытия Mini App."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="🚀 Открыть Jingpt",
            web_app=WebAppInfo(url=MINIAPP_URL),
        )
    ]])


@dp.message(CommandStart())
async def cmd_start(message: Message):
    await db.get_or_create_user(
        user_id    = message.from_user.id,
        username   = message.from_user.username   or "",
        first_name = message.from_user.first_name or "",
        last_name  = message.from_user.last_name  or "",
    )
    await message.answer(
        "👋 Привет! Я <b>Jingpt</b> — AI-ассистент на базе Claude Opus 4.7.\n\n"
        "Открой приложение, чтобы начать общение:",
        parse_mode="HTML",
        reply_markup=main_keyboard(),
    )


@dp.message(F.text)
async def handle_text(message: Message):
    user = await db.get_or_create_user(
        user_id    = message.from_user.id,
        username   = message.from_user.username   or "",
        first_name = message.from_user.first_name or "",
        last_name  = message.from_user.last_name  or "",
    )

    if user["balance"] <= 0:
        await message.answer(
            "⚠️ У вас закончились запросы.\n\n"
            "Пополните баланс в приложении:",
            reply_markup=main_keyboard(),
        )
        return

    await message.answer("⏳ Думаю...")

    try:
        response = claude.messages.create(
            model="claude-opus-4-7",
            max_tokens=2000,
            messages=[{"role": "user", "content": message.text}],
        )
        answer = response.content[0].text
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")
        return

    await db.deduct_balance(message.from_user.id)
    await db.save_message(message.from_user.id, "user",      message.text)
    await db.save_message(message.from_user.id, "assistant", answer)

    updated = await db.get_user(message.from_user.id)
    await message.answer(
        f"{answer}\n\n<i>Осталось запросов: {updated['balance']}</i>",
        parse_mode="HTML",
        reply_markup=main_keyboard(),
    )


async def main():
    await db.init_db()
    print("✅ Jingpt бот запущен!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
