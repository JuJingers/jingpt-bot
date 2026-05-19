"""
Новостной агент Jingpt.
Каждый день собирает топ-3 новости об AI и технологиях,
переписывает их в стиле канала и отправляет администратору.
"""

import asyncio
import os
import feedparser
import httpx
import anthropic
from datetime import datetime, timedelta, timezone

import database as db

# ── Настройки ─────────────────────────────────────────────────────────────────
ADMIN_ID           = 821139020
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_TOKEN",    "8563490950:AAHNoSzdlubomAUPk1M_JG4s8v690ciTNLk")
ANTHROPIC_API_KEY  = os.environ.get("ANTHROPIC_API_KEY", "sk-hub-9iH9yudgwhmrpsB1guWQu2MEfT200hRx")
CLAUDE_BASE_URL    = "https://api.tokenator.cloud/anthropic"
CLAUDE_MODEL       = "claude-opus-4-7"

# ── RSS-источники ─────────────────────────────────────────────────────────────
RSS_FEEDS = [
    ("TechCrunch AI",    "https://techcrunch.com/category/artificial-intelligence/feed/"),
    ("VentureBeat AI",   "https://venturebeat.com/category/ai/feed/"),
    ("The Verge AI",     "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml"),
    ("MIT Tech Review",  "https://www.technologyreview.com/feed/"),
    ("HuggingFace Blog", "https://huggingface.co/blog/feed.xml"),
    ("Ars Technica AI",  "https://feeds.arstechnica.com/arstechnica/index"),
]

claude = anthropic.Anthropic(
    api_key=ANTHROPIC_API_KEY,
    base_url=CLAUDE_BASE_URL,
    http_client=httpx.Client(trust_env=False),
)


# ── Сбор новостей из RSS ──────────────────────────────────────────────────────
async def fetch_articles() -> list[dict]:
    """Собирает свежие статьи за последние 48 часов из всех источников."""
    articles = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=48)

    for source_name, url in RSS_FEEDS:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(url, follow_redirects=True,
                                        headers={"User-Agent": "Mozilla/5.0"})
                if resp.status_code != 200:
                    continue
            feed = feedparser.parse(resp.text)
            for entry in feed.entries[:10]:  # берём последние 10 с каждого источника
                # Проверяем дату
                published = entry.get("published_parsed") or entry.get("updated_parsed")
                if published:
                    pub_dt = datetime(*published[:6], tzinfo=timezone.utc)
                    if pub_dt < cutoff:
                        continue

                article_url = entry.get("link", "")
                if not article_url:
                    continue

                # Пропускаем уже отправленные
                if await db.is_news_url_sent(article_url):
                    continue

                summary = entry.get("summary", "") or entry.get("description", "")
                # Убираем HTML теги
                import re
                summary = re.sub(r"<[^>]+>", "", summary)[:500]

                articles.append({
                    "source":  source_name,
                    "title":   entry.get("title", ""),
                    "url":     article_url,
                    "summary": summary,
                })
        except Exception as e:
            print(f"❌ RSS fetch error [{source_name}]: {e}")

    print(f"📰 Найдено {len(articles)} новых статей")
    return articles


# ── Выбор топ-3 и переписывание в стиле канала ────────────────────────────────
def select_and_rewrite(articles: list[dict]) -> list[str] | None:
    """Claude выбирает топ-3 и переписывает каждую в стиле Telegram-канала."""
    if not articles:
        return None

    # Формируем список статей для Claude
    articles_text = ""
    for i, a in enumerate(articles, 1):
        articles_text += (
            f"[{i}] {a['source']}\n"
            f"Заголовок: {a['title']}\n"
            f"Описание: {a['summary']}\n"
            f"URL: {a['url']}\n\n"
        )

    prompt = f"""Ты — редактор Telegram-канала об AI и технологиях «Jingpt» (@cloudeinJIN).

Вот список свежих статей:

{articles_text}

Твоя задача:
1. Выбери ТОП-3 самые интересные и важные новости для русскоязычной аудитории (нейросети, AI-инструменты, большие языковые модели, технологические прорывы).
2. Для каждой напиши готовый пост в Telegram в стиле канала.

Стиль канала:
- Язык: русский, живой и понятный
- Начинается с яркого эмодзи
- Жирный заголовок
- 3-5 коротких предложений — суть новости, почему это важно
- В конце ссылка на источник и 2-3 хэштега (#AI #нейросети #технологии и подобные)
- Никакого занудства — пишем живо, как рассказываем другу

Верни РОВНО 3 поста, разделённые строкой "---".
Никакого вступления, никаких комментариев — только 3 готовых поста."""

    try:
        response = claude.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        result = response.content[0].text.strip()
        posts = [p.strip() for p in result.split("---") if p.strip()]
        return posts[:3]
    except Exception as e:
        print(f"❌ Claude error: {e}")
        return None


# ── Отправка поста админу через Telegram ──────────────────────────────────────
async def send_to_admin(text: str, post_num: int, total: int):
    """Отправляет пост администратору."""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        header = f"📬 *Пост {post_num}/{total} — готов к публикации*\n\n"
        async with httpx.AsyncClient() as client:
            await client.post(url, json={
                "chat_id":    ADMIN_ID,
                "text":       header + text,
                "parse_mode": "Markdown",
            }, timeout=10)
    except Exception as e:
        print(f"❌ Send to admin error: {e}")


async def send_admin_message(text: str):
    """Отправляет простое сообщение администратору."""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        async with httpx.AsyncClient() as client:
            await client.post(url, json={
                "chat_id":    ADMIN_ID,
                "text":       text,
                "parse_mode": "HTML",
            }, timeout=10)
    except Exception as e:
        print(f"❌ send_admin_message error: {e}")


# ── Главная функция дайджеста ──────────────────────────────────────────────────
async def run_digest():
    """Запускает полный цикл: сбор → выбор → переписывание → отправка."""
    print(f"🗞 Запуск новостного дайджеста [{datetime.now().strftime('%Y-%m-%d %H:%M')}]")

    articles = await fetch_articles()
    if not articles:
        print("📭 Новых статей не найдено")
        await send_admin_message("📭 <b>Дайджест</b>: новых статей не найдено. Попробуй позже.")
        return

    posts = select_and_rewrite(articles)
    if not posts:
        print("❌ Claude не смог обработать статьи")
        await send_admin_message("❌ <b>Дайджест</b>: ошибка при обработке статей.")
        return

    # Помечаем статьи как отправленные
    used_urls = [a["url"] for a in articles[:len(posts)]]
    await db.mark_news_sent(used_urls)

    # Отправляем каждый пост отдельным сообщением
    await send_admin_message(
        f"🗞 <b>Ежедневный дайджест готов!</b>\n\n"
        f"Выбрано {len(posts)} лучших новостей. Отправляю по одному 👇"
    )
    await asyncio.sleep(1)

    for i, post in enumerate(posts, 1):
        await send_to_admin(post, i, len(posts))
        await asyncio.sleep(1)

    print(f"✅ Дайджест отправлен: {len(posts)} постов")


# ── Фоновый цикл (раз в 24 часа в 10:00 по МСК) ──────────────────────────────
async def news_agent_loop():
    """Ежедневно в 10:00 МСК запускает дайджест."""
    await asyncio.sleep(30)  # небольшая задержка при старте

    while True:
        try:
            # Считаем сколько спать до следующих 10:00 МСК (UTC+3)
            now_utc   = datetime.now(timezone.utc)
            now_msk   = now_utc + timedelta(hours=3)
            target    = now_msk.replace(hour=10, minute=0, second=0, microsecond=0)
            if now_msk >= target:
                target += timedelta(days=1)
            sleep_sec = (target - now_msk).total_seconds()
            print(f"⏰ Следующий дайджест через {sleep_sec/3600:.1f} ч ({target.strftime('%Y-%m-%d %H:%M')} МСК)")
            await asyncio.sleep(sleep_sec)
            await run_digest()
        except Exception as e:
            print(f"❌ news_agent_loop error: {e}")
            await asyncio.sleep(3600)  # при ошибке ждём час и пробуем снова
