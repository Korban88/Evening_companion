import asyncio
import logging
from datetime import datetime, timezone, timedelta
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

# ВАЖНО: aiogram 3.7 требует задавать parse_mode через DefaultBotProperties
bot = Bot(
    token=settings.bot_token,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher()

DB_PATH = settings.db_path

# === Инициализация БД ===
CREATE_SQL = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS users (
  user_id INTEGER PRIMARY KEY,
  mode TEXT DEFAULT 'companion',
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS diary (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER,
  ts TEXT,
  text TEXT
);

CREATE TABLE IF NOT EXISTS goals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER,
  title TEXT,
  deadline_ts TEXT,
  done INTEGER DEFAULT 0,
  created_ts TEXT
);

CREATE TABLE IF NOT EXISTS praise_stats (
  user_id INTEGER PRIMARY KEY,
  streak INTEGER DEFAULT 0,
  last_ts TEXT
);
"""

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(CREATE_SQL)
        await db.commit()

# === Клавиатура ===
def main_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Компаньон"), KeyboardButton(text="Дисциплина"), KeyboardButton(text="Похвала")],
            [KeyboardButton(text="Помощь")]
        ],
        resize_keyboard=True
    )

# === Утилиты ===
async def ensure_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT 1 FROM users WHERE user_id=?", (user_id,))
        if not await cur.fetchone():
            await db.execute(
                "INSERT INTO users(user_id, mode, created_at) VALUES(?,?,?)",
                (user_id, "companion", datetime.utcnow().isoformat())
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
        return row[0] if row else "companion"

# === Хэндлеры ===
@dp.message(CommandStart())
async def start(m: Message):
    await ensure_user(m.from_user.id)
    await m.answer(
        "Добро пожаловать во <b>Вечерний Собеседник</b>\n\n"
        "Здесь можно:\n"
        "• Выговориться и сохранить мысли (Компаньон)\n"
        "• Ставить цели и получать напоминания (Дисциплина)\n"
        "• Получать слова поддержки (Похвала)\n\n"
        "Выбери режим кнопкой ниже.",
        reply_markup=main_kb()
    )

@dp.message(F.text.lower() == "компаньон")
async def mode_companion(m: Message):
    await set_mode(m.from_user.id, "companion")
    await m.answer("Режим Компаньон включён. Просто напиши, что у тебя на душе.")

@dp.message(F.text.lower() == "дисциплина")
async def mode_discipline(m: Message):
    await set_mode(m.from_user.id, "discipline")
    await m.answer("Режим Дисциплина включён. Используй /goal чтобы добавить цель.")

@dp.message(F.text.lower() == "похвала")
async def mode_praise(m: Message):
    await set_mode(m.from_user.id, "praise")
    await m.answer("Режим Похвала включён. Напиши что-нибудь — я отвечу поддержкой.")

@dp.message(F.text.lower() == "помощь")
@dp.message(Command("help"))
async def help_cmd(m: Message):
    await m.answer(
        "Команды:\n"
        "/goal <текст> — добавить цель\n"
        "/goals — список целей\n"
        "/done <id> — отметить цель выполненной\n"
        "/summary — краткий итог дня"
    )

# === Обработка сообщений ===
@dp.message(F.text, ~F.text.startswith("/"))
async def route_by_mode(m: Message):
    mode = await get_mode(m.from_user.id)
    if mode == "companion":
        await m.answer("Записал. Хочешь, вечером я напомню подвести итог?")
    elif mode == "discipline":
        await m.answer("Запомнил. Добавь цель через /goal, список — /goals.")
    else:
        await m.answer("Ты молодец. Даже маленький шаг сегодня — это движение вперёд.")

# === Планировщик ===
scheduler = AsyncIOScheduler()

async def daily_jobs():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id, mode FROM users")
        users = await cur.fetchall()
    for uid, mode in users:
        try:
            if mode == "companion":
                await bot.send_message(uid, "Вечерний Собеседник: хочешь итог дня? Напиши /summary.")
            elif mode == "discipline":
                await bot.send_message(uid, "Напоминание: проверь свои цели (/goals).")
            else:
                await bot.send_message(uid, "Забота о себе важна. Ты справляешься.")
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
