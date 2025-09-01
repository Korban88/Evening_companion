import asyncio
import logging
from datetime import datetime, timezone, timedelta, date
from typing import Optional, List, Tuple

import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.filters import CommandStart, Command
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from config import settings

logging.basicConfig(level=logging.INFO)

bot = Bot(
    token=settings.bot_token,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher()

DB_PATH = settings.db_path

# ================== БАЗА ДАННЫХ ==================
CREATE_SQL = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS users (
  user_id INTEGER PRIMARY KEY,
  mode TEXT DEFAULT 'talk',            -- talk | support | motivate
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS diary (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER,
  ts TEXT,
  text TEXT
);

CREATE TABLE IF NOT EXISTS support_stats (
  user_id INTEGER PRIMARY KEY,
  streak INTEGER DEFAULT 0,
  last_ts TEXT
);

CREATE TABLE IF NOT EXISTS motivate_stats (
  user_id INTEGER PRIMARY KEY,
  streak INTEGER DEFAULT 0,
  last_ts TEXT
);
"""

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(CREATE_SQL)
        await db.commit()

# ================== КЛАВИАТУРЫ ==================
def base_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Беседа"), KeyboardButton(text="Поддержка"), KeyboardButton(text="Мотивация")],
            [KeyboardButton(text="Итог дня"), KeyboardButton(text="Помощь")]
        ],
        resize_keyboard=True
    )

# ================== УТИЛИТЫ ПОЛЬЗОВАТЕЛЯ ==================
async def ensure_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT 1 FROM users WHERE user_id=?", (user_id,))
        if not await cur.fetchone():
            await db.execute(
                "INSERT INTO users(user_id, mode, created_at) VALUES(?,?,?)",
                (user_id, "talk", datetime.utcnow().isoformat())
            )
            await db.commit()

async def set_mode(user_id: int, mode: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET mode=? WHERE user_id=?", (mode, user_id))
        await db.commit()

async def get_mode(user_id: int) -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT mode FROM users WHERE user_id=?", (user_id,))
        row = await cur.fetchone()
        return row[0] if row else "talk"

# ================== БЕСЕДА (ДНЕВНИК) ==================
def reflect_short(text: str) -> str:
    t = " ".join(text.strip().split())
    if len(t) > 180:
        t = t[:180] + "…"
    return f"Ты написал: «{t}». Что из этого важно сохранить на завтра?"

async def diary_add(user_id: int, text: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO diary(user_id, ts, text) VALUES(?,?,?)",
            (user_id, datetime.utcnow().isoformat(), text.strip())
        )
        await db.commit()

async def diary_summary(user_id: int) -> str:
    since = datetime.utcnow() - timedelta(days=1)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT ts, text FROM diary WHERE user_id=? AND ts>=? ORDER BY ts DESC",
            (user_id, since.isoformat())
        )
        rows = await cur.fetchall()
    if not rows:
        return "За последние сутки записей нет."
    bullets = []
    for ts, text in rows[:10]:
        t = datetime.fromisoformat(ts).strftime("%H:%M")
        snippet = text.strip().replace("\n", " ")
        if len(snippet) > 90:
            snippet = snippet[:90] + "…"
        bullets.append(f"{t} — {snippet}")
    return "Краткий дневник за сутки:\n" + "\n".join(bullets)

# ================== ПОДДЕРЖКА ==================
SUPPORT_TEMPLATES = [
    "Ты важен. Даже если день тяжёлый, ты не один.",
    "Сегодня ты сделал достаточно. Отдохни, это нормально.",
    "Твоим чувствам есть место. Я рядом текстом, но по-настоящему.",
    "Можно не успевать всё. Важно, что ты стараешься.",
    "Береги себя. Маленький шаг к заботе о себе — уже движение."
]

async def support_phrase(user_id: int) -> str:
    now = datetime.utcnow()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT streak, last_ts FROM support_stats WHERE user_id=?", (user_id,))
        row = await cur.fetchone()
        if not row:
            streak = 1
            await db.execute("INSERT INTO support_stats(user_id, streak, last_ts) VALUES(?,?,?)",
                             (user_id, streak, now.isoformat()))
        else:
            streak, last_ts = row
            if last_ts:
                last = datetime.fromisoformat(last_ts)
                if (now.date() - last.date()).days >= 1:
                    streak += 1
            else:
                streak += 1
            await db.execute("UPDATE support_stats SET streak=?, last_ts=? WHERE user_id=?",
                             (streak, now.isoformat(), user_id))
        await db.commit()
    phrase = SUPPORT_TEMPLATES[hash((user_id, now.date())) % len(SUPPORT_TEMPLATES)]
    return f"{phrase}\nСерия дней с заботой: {streak}"

# ================== МОТИВАЦИЯ ==================
MOTIVATION_TEMPLATES = [
    "Начни с малого. Пять минут сегодня лучше, чем ноль завтра.",
    "Сделай один простой шаг. Остальное подтянется.",
    "Ты справишься. Результат любит регулярность.",
    "Не обязательно идеально. Достаточно по-настоящему.",
    "Выбери одну маленькую вещь и сделай её сейчас."
]

async def motivation_phrase(user_id: int) -> str:
    now = datetime.utcnow()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT streak, last_ts FROM motivate_stats WHERE user_id=?", (user_id,))
        row = await cur.fetchone()
        if not row:
            streak = 1
            await db.execute("INSERT INTO motivate_stats(user_id, streak, last_ts) VALUES(?,?,?)",
                             (user_id, streak, now.isoformat()))
        else:
            streak, last_ts = row
            if last_ts:
                last = datetime.fromisoformat(last_ts)
                if (now.date() - last.date()).days >= 1:
                    streak += 1
            else:
                streak += 1
            await db.execute("UPDATE motivate_stats SET streak=?, last_ts=? WHERE user_id=?",
                             (streak, now.isoformat(), user_id))
        await db.commit()
    phrase = MOTIVATION_TEMPLATES[hash((user_id, now.date(), "m")) % len(MOTIVATION_TEMPLATES)]
    return f"{phrase}\nДней с настроем: {streak}"

# ================== ХЭНДЛЕРЫ ==================
@dp.message(CommandStart())
async def start(m: Message):
    await ensure_user(m.from_user.id)
    await m.answer(
        "Добро пожаловать во <b>Вечерний Собеседник</b>\n\n"
        "Режимы:\n"
        "• Беседа — говори, я слышу и сохраняю в дневник\n"
        "• Поддержка — тёплые слова, когда тяжело\n"
        "• Мотивация — короткий заряд на действие\n\n"
        "Выбери режим или просто напиши.",
        reply_markup=base_kb()
    )

@dp.message(F.text.lower() == "беседа")
async def mode_talk(m: Message):
    await ensure_user(m.from_user.id)
    await set_mode(m.from_user.id, "talk")
    await m.answer("Режим Беседа. Пиши, что на душе.", reply_markup=base_kb())

@dp.message(F.text.lower() == "поддержка")
async def mode_support(m: Message):
    await ensure_user(m.from_user.id)
    await set_mode(m.from_user.id, "support")
    txt = await support_phrase(m.from_user.id)
    await m.answer("Режим Поддержка.\n" + txt, reply_markup=base_kb())

@dp.message(F.text.lower() == "мотивация")
async def mode_motivate(m: Message):
    await ensure_user(m.from_user.id)
    await set_mode(m.from_user.id, "motivate")
    txt = await motivation_phrase(m.from_user.id)
    await m.answer("Режим Мотивация.\n" + txt, reply_markup=base_kb())

@dp.message(F.text.lower() == "итог дня")
@dp.message(Command("summary"))
async def summary_cmd(m: Message):
    txt = await diary_summary(m.from_user.id)
    await m.answer(txt, reply_markup=base_kb())

@dp.message(F.text.lower() == "помощь")
@dp.message(Command("help"))
async def help_cmd(m: Message):
    await m.answer(
        "Режимы:\n"
        "— Беседа: просто пиши, я отвечу и сохраню в дневник. «Итог дня» покажет последние записи.\n"
        "— Поддержка: тёплые фразы, снижающие тревогу. Ведём серию заботы.\n"
        "— Мотивация: короткие фразы, чтобы сдвинуться с места. Ведём серию настроя.\n"
        "Кнопки снизу — для переключения."
    )

# Свободный текст
@dp.message(F.text, ~F.text.startswith("/"))
async def route_free_text(m: Message):
    await ensure_user(m.from_user.id)
    mode = await get_mode(m.from_user.id)

    if mode == "talk":
        await diary_add(m.from_user.id, m.text)
        await m.answer(reflect_short(m.text), reply_markup=base_kb())
        return

    if mode == "support":
        txt = await support_phrase(m.from_user.id)
        await m.answer(txt, reply_markup=base_kb())
        return

    # motivate
    txt = await motivation_phrase(m.from_user.id)
    await m.answer(txt, reply_markup=base_kb())

# ================== ПЛАНИРОВЩИК ==================
scheduler = AsyncIOScheduler()

async def daily_jobs():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id, mode FROM users")
        users = await cur.fetchall()
    for uid, mode in users:
        try:
            if mode == "talk":
                await bot.send_message(uid, "Вечерний Собеседник. Хочешь итог дня? Нажми «Итог дня».")
            elif mode == "support":
                txt = await support_phrase(uid)
                await bot.send_message(uid, "Немного поддержки на вечер.\n" + txt)
            else:
                txt = await motivation_phrase(uid)
                await bot.send_message(uid, "Вечерний настрой.\n" + txt)
        except Exception as e:
            logging.warning(f"daily_jobs error for {uid}: {e}")

async def on_startup():
    await init_db()
    hour_utc = (settings.daily_hour_msk - 3) % 24
    scheduler.add_job(daily_jobs, CronTrigger(hour=hour_utc, minute=0))
    scheduler.start()
    logging.info("Scheduler started")

async def main():
    await on_startup()
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Stopped")
