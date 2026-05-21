import asyncio
import base64
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
from news_agent import news_agent_loop, run_digest

# ── Настройки ─────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN    = os.environ.get("TELEGRAM_TOKEN", "8563490950:AAHNoSzdlubomAUPk1M_JG4s8v690ciTNLk")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "sk-hub-9iH9yudgwhmrpsB1guWQu2MEfT200hRx")
OPENAI_API_KEY    = os.environ.get("OPENAI_API_KEY", "")   # для Whisper (голосовые)
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
        f"📄 Отправь PDF или фото — Claude прочитает\n"
        f"🎤 Отправь голосовое — расшифрую и отвечу\n"
        f"✍️ Тексты, код, переводы (4 режима в приложении)\n"
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
        "• Общение с AI через приложение (4 режима!)\n"
        "• Отправь фото или PDF — Claude проанализирует 🖼\n"
        "• Отправь голосовое — Claude ответит 🎤\n"
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


# ── Утилиты для бот-ответов ───────────────────────────────────────────────────
def split_text(text: str, max_len: int = 4000) -> list[str]:
    """Разбивает длинный текст на части для Telegram."""
    if len(text) <= max_len:
        return [text]
    parts = []
    while text:
        parts.append(text[:max_len])
        text = text[max_len:]
    return parts


async def ask_claude_bot(user_id: int, user_content) -> str:
    """Отправляет запрос Claude и возвращает ответ (для бот-обработчиков)."""
    chat = await db.get_or_create_default_chat(user_id)
    history = await db.get_chat_history(user_id, limit=6, chat_id=chat["id"])
    messages = [{"role": h["role"], "content": h["content"]} for h in history]
    messages.append({"role": "user", "content": user_content})
    response = claude.messages.create(
        model="claude-opus-4-7",
        max_tokens=1500,
        system="Ты — Jingpt, умный и дружелюбный AI-ассистент. Будь конкретным, полезным и по делу.",
        messages=messages,
    )
    return response.content[0].text


async def transcribe_voice(audio_bytes: bytes) -> str | None:
    """Распознаёт голосовое через OpenAI Whisper."""
    if not OPENAI_API_KEY:
        return None
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                files={"file": ("voice.ogg", audio_bytes, "audio/ogg")},
                data={"model": "whisper-1", "language": "ru"},
            )
        if resp.status_code == 200:
            return resp.json().get("text", "").strip()
        print(f"Whisper error: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        print(f"transcribe_voice error: {e}")
    return None


# ── 🎤 Голосовые сообщения ────────────────────────────────────────────────────
@dp.message(F.voice)
async def handle_voice(message: Message):
    user = await db.get_or_create_user(
        user_id=message.from_user.id,
        username=message.from_user.username or "",
        first_name=message.from_user.first_name or "",
        last_name=message.from_user.last_name or "",
    )
    if user.get("is_blocked"):
        await message.answer("🚫 Ваш аккаунт заблокирован.")
        return

    if not OPENAI_API_KEY:
        await message.answer(
            "🎤 Голосовые сообщения пока не настроены.\n"
            "Используй приложение для общения 👇",
            reply_markup=main_keyboard(),
        )
        return

    can_use, sub = await db.use_request(message.from_user.id)
    if not can_use:
        await message.answer("⚠️ Лимит запросов исчерпан. Оформите подписку:", reply_markup=buy_keyboard())
        return

    wait_msg = await message.answer("🎤 Распознаю голосовое…")
    try:
        file_info = await bot.get_file(message.voice.file_id)
        bio = await bot.download_file(file_info.file_path)
        transcript = await transcribe_voice(bio.read())

        if not transcript:
            await wait_msg.edit_text("❌ Не удалось распознать. Попробуй ещё раз или пиши текстом.")
            return

        await wait_msg.edit_text(f"🎤 _{transcript}_\n\n⏳ Думаю…", parse_mode="Markdown")

        chat = await db.get_or_create_default_chat(message.from_user.id)
        answer = await ask_claude_bot(message.from_user.id, transcript)
        await db.save_message(message.from_user.id, "user", transcript, chat["id"])
        await db.save_message(message.from_user.id, "assistant", answer, chat["id"])

        header = f"🎤 *Ты:* _{transcript}_\n\n"
        parts = split_text(header + answer)
        await wait_msg.edit_text(parts[0], parse_mode="Markdown")
        for part in parts[1:]:
            await message.answer(part)
    except Exception as e:
        print(f"handle_voice error: {e}")
        await wait_msg.edit_text("❌ Ошибка при обработке голосового. Попробуй ещё раз.")


# ── 🖼 Фото ───────────────────────────────────────────────────────────────────
@dp.message(F.photo)
async def handle_photo(message: Message):
    user = await db.get_or_create_user(
        user_id=message.from_user.id,
        username=message.from_user.username or "",
        first_name=message.from_user.first_name or "",
        last_name=message.from_user.last_name or "",
    )
    if user.get("is_blocked"):
        await message.answer("🚫 Ваш аккаунт заблокирован.")
        return

    can_use, sub = await db.use_request(message.from_user.id)
    if not can_use:
        await message.answer("⚠️ Лимит запросов исчерпан. Оформите подписку:", reply_markup=buy_keyboard())
        return

    caption = message.caption or "Опиши подробно что на этом изображении."
    wait_msg = await message.answer("🖼 Анализирую изображение…")
    try:
        photo = message.photo[-1]  # берём наибольшее разрешение
        file_info = await bot.get_file(photo.file_id)
        bio = await bot.download_file(file_info.file_path)
        b64 = base64.b64encode(bio.read()).decode()

        content = [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
            {"type": "text", "text": caption},
        ]
        chat = await db.get_or_create_default_chat(message.from_user.id)
        answer = await ask_claude_bot(message.from_user.id, content)
        await db.save_message(message.from_user.id, "user", f"[Фото]: {caption}", chat["id"])
        await db.save_message(message.from_user.id, "assistant", answer, chat["id"])

        parts = split_text(answer)
        await wait_msg.edit_text(parts[0])
        for part in parts[1:]:
            await message.answer(part)
    except Exception as e:
        print(f"handle_photo error: {e}")
        await wait_msg.edit_text("❌ Ошибка при анализе изображения. Попробуй ещё раз.")


# ── 📎 Документы ──────────────────────────────────────────────────────────────
@dp.message(F.document)
async def handle_document(message: Message):
    user = await db.get_or_create_user(
        user_id=message.from_user.id,
        username=message.from_user.username or "",
        first_name=message.from_user.first_name or "",
        last_name=message.from_user.last_name or "",
    )
    if user.get("is_blocked"):
        await message.answer("🚫 Ваш аккаунт заблокирован.")
        return

    doc = message.document
    allowed_types = ("application/pdf", "image/jpeg", "image/png", "image/webp", "image/gif")
    if doc.mime_type not in allowed_types:
        await message.answer(
            "📎 Поддерживаемые форматы: PDF, JPG, PNG, WEBP\n\n"
            "Для других файлов используй приложение 👇",
            reply_markup=main_keyboard(),
        )
        return

    can_use, sub = await db.use_request(message.from_user.id)
    if not can_use:
        await message.answer("⚠️ Лимит запросов исчерпан. Оформите подписку:", reply_markup=buy_keyboard())
        return

    caption = message.caption or (
        "Проанализируй этот документ подробно." if doc.mime_type == "application/pdf"
        else "Опиши что на изображении."
    )
    wait_msg = await message.answer("📎 Анализирую файл…")
    try:
        file_info = await bot.get_file(doc.file_id)
        bio = await bot.download_file(file_info.file_path)
        b64 = base64.b64encode(bio.read()).decode()

        if doc.mime_type == "application/pdf":
            content = [
                {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": b64}},
                {"type": "text", "text": caption},
            ]
        else:
            content = [
                {"type": "image", "source": {"type": "base64", "media_type": doc.mime_type, "data": b64}},
                {"type": "text", "text": caption},
            ]

        chat = await db.get_or_create_default_chat(message.from_user.id)
        answer = await ask_claude_bot(message.from_user.id, content)
        await db.save_message(message.from_user.id, "user", f"[Файл: {doc.file_name}]: {caption}", chat["id"])
        await db.save_message(message.from_user.id, "assistant", answer, chat["id"])

        parts = split_text(answer)
        await wait_msg.edit_text(parts[0])
        for part in parts[1:]:
            await message.answer(part)
    except Exception as e:
        print(f"handle_document error: {e}")
        await wait_msg.edit_text("❌ Ошибка при анализе файла. Попробуй ещё раз.")


# ── Текстовые сообщения ───────────────────────────────────────────────────────
@dp.message(F.text)
async def handle_text(message: Message):
    text = message.text or ""

    # Секретная команда дайджеста только для админа
    if text.startswith("/digest") and message.from_user.id == ADMIN_ID:
        await message.answer("🗞 Запускаю сбор новостей, подожди пару минут…")
        asyncio.create_task(run_digest())
        return

    # Все остальные неизвестные команды — молча игнорируем
    if text.startswith("/"):
        return

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


# ── /digest — ручной запуск дайджеста (только для админа) ────────────────────
ADMIN_ID = 821139020

@dp.message(Command("digest"))
async def cmd_digest(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    await message.answer("🗞 Запускаю сбор новостей, подожди пару минут…")
    asyncio.create_task(run_digest())


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
    await asyncio.gather(run_server(), run_bot(), ref_reminder_loop(), news_agent_loop())

if __name__ == "__main__":
    asyncio.run(main())
