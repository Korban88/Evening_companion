import asyncio
import logging
import random
from datetime import datetime, timezone, timedelta
from typing import List, Tuple, Optional

import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.filters import CommandStart, Command
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode, ChatAction
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from config import settings
from generator import generate_talk_reply, generate_support_reply, generate_motivation_reply

logging.basicConfig(level=logging.INFO)

bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

DB_PATH = settings.db_path

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
  role TEXT,     -- user | assistant
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

CREATE TABLE IF NOT EXISTS billing (
  user_id INTEGER PRIMARY KEY,
  sub_until TEXT,
  trial_left INTEGER DEFAULT 0
);
"""

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(CREATE_SQL)
        # инициализация trial для новых пользователей будет при ensure_user
        await db.commit()

def base_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Беседа"), KeyboardButton(text="Поддержка"), KeyboardButton(text="Мотивация")],
            [KeyboardButton(text="Итог дня"), KeyboardButton(text="Подписка"), KeyboardButton(text="Помощь")]
        ],
        resize_keyboard=True
    )

async def ensure_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT 1 FROM users WHERE user_id=?", (user_id,))
        if not await cur.fetchone():
            await db.execute("INSERT INTO users(user_id, mode, created_at) VALUES(?,?,?)",
                             (user_id, "talk", datetime.now(timezone.utc).isoformat()))
        cur = await db.execute("SELECT 1 FROM billing WHERE user_id=?", (user_id,))
        if not await cur.fetchone():
            await db.execute("INSERT INTO billing(user_id, sub_until, trial_left) VALUES(?,?,?)",
                             (user_id, None, settings.trial_messages))
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

async def diary_add(user_id: int, role: str, text: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO diary(user_id, ts, role, text) VALUES(?,?,?,?)",
                         (user_id, datetime.now(timezone.utc).isoformat(), role, text.strip()))
        await db.commit()

async def diary_summary(user_id: int) -> str:
    since = datetime.now(timezone.utc) - timedelta(days=1)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT ts, text FROM diary WHERE user_id=? AND role='user' AND ts>=? ORDER BY ts DESC",
            (user_id, since.isoformat()))
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

async def recent_history_pairs(user_id: int, limit: int) -> List[Tuple[str, str]]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT role, text FROM diary WHERE user_id=? ORDER BY id DESC LIMIT ?",
                               (user_id, limit))
        rows = await cur.fetchall()
    rows = rows[::-1]
    return [(r, t) for r, t in rows]

def extract_prev_messages(history: List[Tuple[str, str]]) -> tuple[Optional[str], Optional[str]]:
    if not history:
        return None, None
    trimmed = history[:-1]
    prev_user = None
    last_assistant = None
    for role, text in reversed(trimmed):
        if last_assistant is None and role == "assistant":
            last_assistant = text
        if prev_user is None and role == "user":
            prev_user = text
        if prev_user is not None and last_assistant is not None:
            break
    return prev_user, last_assistant

async def appear_typing(chat_id: int, min_s=0.6, max_s=1.4):
    await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    await asyncio.sleep(random.uniform(min_s, max_s))

# ---------- Подписка ----------
async def has_access(user_id: int, consume_trial: bool = True) -> bool:
    now = datetime.now(timezone.utc)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT sub_until, trial_left FROM billing WHERE user_id=?", (user_id,))
        row = await cur.fetchone()
        if not row:
            return False
        sub_until, trial_left = row
        if sub_until:
            try:
                if datetime.fromisoformat(sub_until) > now:
                    return True
            except Exception:
                pass
        if trial_left and trial_left > 0:
            if consume_trial:
                await db.execute("UPDATE billing SET trial_left=trial_left-1 WHERE user_id=?", (user_id,))
                await db.commit()
            return True
        return False

async def grant_subscription(user_id: int, days: int):
    until = datetime.now(timezone.utc) + timedelta(days=days)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO billing(user_id, sub_until, trial_left) VALUES(?,?,?) "
                         "ON CONFLICT(user_id) DO UPDATE SET sub_until=excluded.sub_until",
                         (user_id, until.isoformat(), 0))
        await db.commit()

async def billing_status_text(user_id: int) -> str:
    now = datetime.now(timezone.utc)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT sub_until, trial_left FROM billing WHERE user_id=?", (user_id,))
        row = await cur.fetchone()
    if not row:
        return "Подписка: нет данных."
    sub_until, trial_left = row
    if sub_until:
        try:
            dt = datetime.fromisoformat(sub_until)
            if dt > now:
                left = (dt - now).days
                return f"Подписка активна. Осталось дней: {left}."
        except Exception:
            pass
    return f"Подписка не активна. Остаток пробных сообщений: {trial_left or 0}."

def paywall_text() -> str:
    return (
        "Чтобы продолжить общение через выбранную модель, оформи подписку 200 ₽/мес.\n"
        f"Оплата: {settings.payment_url}\n"
        "После оплаты я активирую доступ. Если есть вопросы — напиши."
    )

# ---------- Хэндлеры ----------
@dp.message(CommandStart())
async def start(m: Message):
    await ensure_user(m.from_user.id)
    await m.answer(
        "Добро пожаловать во <b>Вечерний Собеседник</b>\n\n"
        "Режимы:\n"
        "• Беседа — живой разговор на уровне друга\n"
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
    if not await has_access(m.from_user.id, consume_trial=False):
        await m.answer(paywall_text(), reply_markup=base_kb()); return
    reply = await generate_support_reply("")
    await diary_add(m.from_user.id, "assistant", reply)
    await appear_typing(m.chat.id)
    await m.answer("Режим Поддержка.\n" + reply, reply_markup=base_kb())

@dp.message(F.text.lower() == "мотивация")
async def mode_motivate(m: Message):
    await ensure_user(m.from_user.id)
    await set_mode(m.from_user.id, "motivate")
    if not await has_access(m.from_user.id, consume_trial=False):
        await m.answer(paywall_text(), reply_markup=base_kb()); return
    reply = await generate_motivation_reply(None)
    await diary_add(m.from_user.id, "assistant", reply)
    await appear_typing(m.chat.id)
    await m.answer("Режим Мотивация.\n" + reply, reply_markup=base_kb())

@dp.message(F.text.lower() == "итог дня")
@dp.message(Command("summary"))
async def summary_cmd(m: Message):
    txt = await diary_summary(m.from_user.id)
    await m.answer(txt, reply_markup=base_kb())

@dp.message(F.text.lower() == "подписка")
@dp.message(Command("status"))
async def status_cmd(m: Message):
    await m.answer(await billing_status_text(m.from_user.id), reply_markup=base_kb())

@dp.message(Command("grant"))
async def grant_cmd(m: Message):
    if settings.admin_id and m.from_user.id == settings.admin_id:
        try:
            parts = m.text.split()
            days = int(parts[1]) if len(parts) > 1 else 30
            target = int(parts[2]) if len(parts) > 2 else m.from_user.id
            await grant_subscription(target, days)
            await m.answer(f"Выдал подписку на {days} дн. пользователю {target}.")
        except Exception as e:
            await m.answer(f"Ошибка: {e}\nИспользование: /grant <days> [user_id]")
    else:
        await m.answer("Команда недоступна.")

@dp.message(F.text.lower() == "помощь")
@dp.message(Command("help"))
async def help_cmd(m: Message):
    await m.answer(
        "Пиши обычным текстом. Я помню недавний контекст и отвечаю по-человечески.\n"
        "«Подписка» — статус доступа и ссылка на оплату."
    )

@dp.message(F.text, ~F.text.startswith("/"))
async def route_free_text(m: Message):
    await ensure_user(m.from_user.id)
    mode = await get_mode(m.from_user.id)
    await diary_add(m.from_user.id, "user", m.text)

    # доступ к общению с моделью
    if not await has_access(m.from_user.id, consume_trial=True):
        await m.answer(paywall_text(), reply_markup=base_kb()); return

    if mode == "talk":
        history = await recent_history_pairs(m.from_user.id, settings.history_max_msgs)
        prev_user, last_assistant = extract_prev_messages(history)
        reply = await generate_talk_reply(m.text, history, prev_user, last_assistant)
        await diary_add(m.from_user.id, "assistant", reply)
        await appear_typing(m.chat.id)
        await m.answer(reply, reply_markup=base_kb()); return

    if mode == "support":
        reply = await generate_support_reply(m.text)
        await diary_add(m.from_user.id, "assistant", reply)
        await appear_typing(m.chat.id)
        await m.answer(reply, reply_markup=base_kb()); return

    reply = await generate_motivation_reply(m.text)
    await diary_add(m.from_user.id, "assistant", reply)
    await appear_typing(m.chat.id)
    await m.answer(reply, reply_markup=base_kb())

# ---------- Планировщик ----------
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
                reply = await generate_support_reply("")
                await bot.send_message(uid, "Немного поддержки на вечер.\n" + reply)
            else:
                reply = await generate_motivation_reply(None)
                await bot.send_message(uid, "Вечерний настрой.\n" + reply)
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
