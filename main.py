import asyncio
import os
import httpx
import uvicorn
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from aiogram.filters import Command, CommandStart, CommandObject
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
BOT_USERNAME      = ""   # заполняется при старте

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


# ── Отложенное напоминание о бонусе ──────────────────────────────────────────
async def _send_bonus_reminder(user_id: int, name: str):
    """Через 5 минут напоминает о бонусе если пользователь ещё не получил его."""
    await asyncio.sleep(300)  # 5 минут
    user = await db.get_user(user_id)
    if not user or user.get("channel_bonus_claimed") or user.get("is_blocked"):
        return
    try:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎁 Получить +5 запросов", web_app=WebAppInfo(url=MINIAPP_URL))],
        ])
        await bot.send_message(
            user_id,
            f"🎁 <b>{name}, не забудь про бонус!</b>\n\n"
            f"Подпишись на наш канал <b>@cloudeinJIN</b> и получи "
            f"<b>+5 бесплатных запросов</b> прямо сейчас.\n\n"
            f"Открой приложение → Профиль → кнопка «+5 запросов» 👇",
            parse_mode="HTML",
            reply_markup=keyboard,
        )
    except Exception:
        pass


# ── /start ────────────────────────────────────────────────────────────────────
@dp.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject):
    user = await db.get_or_create_user(
        user_id    = message.from_user.id,
        username   = message.from_user.username   or "",
        first_name = message.from_user.first_name or "",
        last_name  = message.from_user.last_name  or "",
    )
    if user.get("is_blocked"):
        await message.answer("🚫 Ваш аккаунт заблокирован.")
        return

    # Обработка реферальной ссылки (/start ref_XXXXXXXX)
    args = command.args or ""
    if args.startswith("ref_"):
        code = args[4:]
        referrer = await db.get_user_by_referral_code(code)
        if referrer and referrer["user_id"] != message.from_user.id:
            await db.set_referred_by(message.from_user.id, referrer["user_id"])

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
        f"🆓 Тариф: <b>Бесплатно</b> — 10 запросов в месяц\n"
        f"🎁 <b>/bonus</b> — получи +5 запросов бесплатно!\n"
        f"👥 <b>/ref</b> — пригласи друга и получи Старт!\n\n"
        f"📋 <b>Команды:</b>\n"
        f"/plan — моя подписка\n"
        f"/bonus — бесплатные запросы\n"
        f"/ref — реферальная программа\n"
        f"/help — тарифы и помощь\n\n"
        f"Нажми кнопку ниже и начни общение 👇",
        parse_mode="HTML",
        reply_markup=main_keyboard(),
    )

    # Отложенное напоминание о бонусе (через 5 минут) если ещё не получил
    if not user.get("channel_bonus_claimed"):
        asyncio.create_task(_send_bonus_reminder(message.from_user.id, name))


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
        "/bonus — получить +5 запросов бесплатно\n"
        "/ref — пригласить друга и получить Старт\n"
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


# ── /ref ──────────────────────────────────────────────────────────────────────
@dp.message(Command("ref"))
async def cmd_ref(message: Message):
    user = await db.get_or_create_user(
        user_id    = message.from_user.id,
        username   = message.from_user.username   or "",
        first_name = message.from_user.first_name or "",
        last_name  = message.from_user.last_name  or "",
    )
    if user.get("is_blocked"):
        await message.answer("🚫 Ваш аккаунт заблокирован.")
        return

    code  = await db.get_or_create_referral_code(message.from_user.id)
    stats = await db.get_referral_stats(message.from_user.id)
    link  = f"https://t.me/{BOT_USERNAME}?start=ref_{code}"

    await message.answer(
        f"👥 <b>Реферальная программа</b>\n\n"
        f"Поделись ссылкой с другом. Когда он оформит любую платную подписку — "
        f"ты получишь тариф ⚡ <b>Старт</b> на 30 дней бесплатно!\n\n"
        f"🔗 Твоя ссылка:\n<code>{link}</code>\n\n"
        f"📊 Статистика:\n"
        f"• Перешли по ссылке: <b>{stats['total']}</b>\n"
        f"• Купили подписку: <b>{stats['rewarded']}</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="📤 Поделиться", url=f"https://t.me/share/url?url={link}&text=Попробуй%20Jingpt%20—%20AI%20в%20Telegram!")
        ]])
    )


# ── /bonus ────────────────────────────────────────────────────────────────────
@dp.message(Command("bonus"))
async def cmd_bonus(message: Message):
    user = await db.get_or_create_user(
        user_id    = message.from_user.id,
        username   = message.from_user.username   or "",
        first_name = message.from_user.first_name or "",
        last_name  = message.from_user.last_name  or "",
    )
    if user.get("is_blocked"):
        await message.answer("🚫 Ваш аккаунт заблокирован.")
        return

    if user.get("channel_bonus_claimed"):
        sub = await db.get_subscription(message.from_user.id)
        rem = sub.get("remaining", 0)
        await message.answer(
            "✅ Вы уже получили бонус за подписку!\n\n"
            f"Осталось запросов: <b>{rem}</b>",
            parse_mode="HTML",
            reply_markup=main_keyboard(),
        )
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Подписаться на канал", url="https://t.me/cloudeinJIN")],
        [InlineKeyboardButton(text="✅ Я подписался — получить +5 запросов",
                              web_app=WebAppInfo(url=MINIAPP_URL))],
    ])
    await message.answer(
        "🎁 <b>Получи +5 запросов бесплатно!</b>\n\n"
        "1️⃣ Подпишись на наш канал — @cloudeinJIN\n"
        "2️⃣ Нажми кнопку ниже чтобы подтвердить\n\n"
        "Бонус начисляется один раз и действует до конца месяца 🔥",
        parse_mode="HTML",
        reply_markup=keyboard,
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


# ── Периодические напоминания о рефералке ────────────────────────────────────
async def ref_reminder_loop():
    """Каждые 3 дня отправляет напоминание о реферальной программе."""
    await asyncio.sleep(60)  # небольшая задержка при старте
    while True:
        try:
            users = await db.get_users_for_ref_reminder(interval_days=3)
            print(f"📢 Реферальная рассылка: {len(users)} пользователей")
            for user in users:
                uid  = user["user_id"]
                name = user.get("first_name") or "друг"
                try:
                    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
                        InlineKeyboardButton(
                            text="👥 Получить реферальную ссылку",
                            web_app=WebAppInfo(url=MINIAPP_URL)
                        )
                    ]])
                    await bot.send_message(
                        uid,
                        f"👥 <b>{name}, зови друзей — получай Старт бесплатно!</b>\n\n"
                        f"Поделись своей реферальной ссылкой. "
                        f"Как только друг оформит любую платную подписку — "
                        f"ты получишь ⚡ <b>Старт на 30 дней</b> в подарок.\n\n"
                        f"Ссылку найдёшь в приложении → Профиль 👇",
                        parse_mode="HTML",
                        reply_markup=keyboard,
                    )
                    await db.mark_ref_reminder_sent(uid)
                    await asyncio.sleep(0.1)  # небольшая пауза между сообщениями
                except Exception as e:
                    print(f"ref_reminder send error uid={uid}: {e}")
        except Exception as e:
            print(f"ref_reminder_loop error: {e}")

        # Ждём 3 дня до следующей рассылки
        await asyncio.sleep(3 * 24 * 60 * 60)


# ── Запуск ────────────────────────────────────────────────────────────────────
async def run_bot():
    global BOT_USERNAME
    await db.init_db()
    me = await bot.get_me()
    BOT_USERNAME = me.username or ""
    print(f"✅ Бот запущен! @{BOT_USERNAME}")
    await dp.start_polling(bot)

async def run_server():
    config = uvicorn.Config(app, host="0.0.0.0", port=PORT, log_level="info")
    server = uvicorn.Server(config)
    print(f"✅ Сервер запущен на порту {PORT}")
    await server.serve()

async def main():
    await asyncio.gather(run_server(), run_bot(), ref_reminder_loop())

if __name__ == "__main__":
    asyncio.run(main())
