import asyncio
import logging
import re
from datetime import datetime, timezone, timedelta, date
from typing import Optional, List, Tuple

import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
)
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
  mode TEXT DEFAULT 'companion',      -- companion | discipline | praise
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

-- ожидаем следующий шаг диалога, чтобы обойтись без команд
CREATE TABLE IF NOT EXISTS pending (
  user_id INTEGER PRIMARY KEY,
  action TEXT,          -- add_goal | mark_done | none
  created_ts TEXT
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
            [KeyboardButton(text="Компаньон"), KeyboardButton(text="Дисциплина"), KeyboardButton(text="Похвала")],
            [KeyboardButton(text="Итог дня"), KeyboardButton(text="Помощь")]
        ],
        resize_keyboard=True
    )

def discipline_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Добавить цель"), KeyboardButton(text="Мои цели")],
            [KeyboardButton(text="Отметить выполненной"), KeyboardButton(text="Итог дня")],
            [KeyboardButton(text="Компаньон"), KeyboardButton(text="Похвала")]
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

async def set_pending(user_id: int, action: Optional[str]):
    async with aiosqlite.connect(DB_PATH) as db:
        if action:
            await db.execute(
                "INSERT INTO pending(user_id, action, created_ts) VALUES(?,?,?) "
                "ON CONFLICT(user_id) DO UPDATE SET action=excluded.action, created_ts=excluded.created_ts",
                (user_id, action, datetime.utcnow().isoformat())
            )
        else:
            await db.execute("DELETE FROM pending WHERE user_id=?", (user_id,))
        await db.commit()

async def get_pending(user_id: int) -> Optional[str]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT action FROM pending WHERE user_id=?", (user_id,))
        row = await cur.fetchone()
        return row[0] if row else None

# ================== КОМПАНЬОН ==================
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

# ================== ДИСЦИПЛИНА ==================
# Простая эвристика: распознаём дедлайны в русской речи
WEEKDAYS = {
    "понедельник": 0, "вторник": 1, "среда": 2, "четверг": 3, "пятница": 4, "суббота": 5, "воскресенье": 6
}

def parse_deadline(text: str, now_utc3: datetime) -> Optional[str]:
    low = text.lower()

    # сегодня / завтра
    if "сегодня" in low:
        return now_utc3.strftime("%Y-%m-%d")
    if "завтра" in low:
        return (now_utc3 + timedelta(days=1)).strftime("%Y-%m-%d")

    # к пятнице / в понедельник
    for wd, idx in WEEKDAYS.items():
        if wd in low:
            ahead = (idx - now_utc3.weekday()) % 7
            ahead = ahead or 7
            return (now_utc3 + timedelta(days=ahead)).strftime("%Y-%m-%d")

    # дата вида 31.12 или 31.12.2025
    m = re.search(r"(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?", low)
    if m:
        day, month, year = m.group(1), m.group(2), m.group(3)
        if year is None:
            year = str(now_utc3.year)
        try:
            d = date(int(year), int(month), int(day))
            return d.strftime("%Y-%m-%d")
        except ValueError:
            pass

    return None

async def add_goal(user_id: int, title: str, deadline_iso: Optional[str]):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO goals(user_id,title,deadline_ts,created_ts) VALUES(?,?,?,?)",
            (user_id, title.strip(), deadline_iso, datetime.utcnow().isoformat())
        )
        await db.commit()

async def list_goals(user_id: int, include_done: bool=False) -> List[Tuple[int, str, Optional[str], int]]:
    q = "SELECT id, title, deadline_ts, done FROM goals WHERE user_id=? "
    if not include_done:
        q += "AND done=0 "
    q += "ORDER BY done, id DESC"
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(q, (user_id,))
        return await cur.fetchall()

def format_goals(rows: List[Tuple[int, str, Optional[str], int]]) -> str:
    if not rows:
        return "Целей пока нет."
    lines = []
    for g_id, title, dl, done in rows:
        mark = "✓" if done else "•"
        dl_t = f" до {dl}" if dl else ""
        lines.append(f"{mark} {g_id}: {title}{dl_t}")
    return "\n".join(lines)

async def mark_done(user_id: int, goal_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("UPDATE goals SET done=1 WHERE user_id=? AND id=? AND done=0", (user_id, goal_id))
        await db.commit()
        return cur.rowcount > 0

def done_inline_kb(rows: List[Tuple[int, str, Optional[str], int]], limit: int = 6) -> InlineKeyboardMarkup:
    buttons = []
    for g_id, title, dl, done in rows[:limit]:
        label = f"{g_id}: {title[:22]}{'…' if len(title)>22 else ''}"
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"done:{g_id}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Нет активных целей", callback_data="noop")]])

# Простейший «интент»: если в режиме Дисциплина текст похож на цель — сохраняем
def looks_like_goal(text: str) -> bool:
    low = text.lower().strip()
    if len(low) < 4:
        return False
    # маркеры: «сделать», «закончить», «выучить», «сдать», «написать», «позвонить», «купить», «пробежать», «похудеть»
    markers = ["сделать", "законч", "выуч", "сдать", "напис", "позвон", "купить", "пробеж", "прочит", "убрат", "оформ", "подать"]
    return any(m in low for m in markers)

# ================== ПОХВАЛА ==================
PRAISE_TEMPLATES = [
    "Сегодня ты сделал достаточно. Даже если кажется иначе — прогресс есть.",
    "Делаешь по-настоящему, а не идеально — это сила.",
    "Держишь фокус на важном. Это видно.",
    "Ты есть у своих близких — и это уже ценность.",
    "Маленький шаг сегодня экономит большие завтра."
]

async def praise_for(user_id: int) -> str:
    now = datetime.utcnow()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT streak, last_ts FROM praise_stats WHERE user_id=?", (user_id,))
        row = await cur.fetchone()
        if not row:
            streak = 1
            await db.execute("INSERT INTO praise_stats(user_id,streak,last_ts) VALUES(?,?,?)",
                             (user_id, streak, now.isoformat()))
        else:
            streak, last_ts = row
            if last_ts:
                last = datetime.fromisoformat(last_ts)
                if (now.date() - last.date()).days >= 1:
                    streak += 1
            else:
                streak += 1
            await db.execute("UPDATE praise_stats SET streak=?, last_ts=? WHERE user_id=?",
                             (streak, now.isoformat(), user_id))
        await db.commit()
    phrase = PRAISE_TEMPLATES[hash((user_id, now.date())) % len(PRAISE_TEMPLATES)]
    return f"{phrase}\nСерия дней с заботой о себе: {streak}"

# ================== ХЭНДЛЕРЫ СТАРТА И РЕЖИМОВ ==================
@dp.message(CommandStart())
async def start(m: Message):
    await ensure_user(m.from_user.id)
    await set_pending(m.from_user.id, None)
    await m.answer(
        "Добро пожаловать во <b>Вечерний Собеседник</b>\n\n"
        "Три режима:\n"
        "• Компаньон — говори, я сохраню в дневник\n"
        "• Дисциплина — цели, сроки, отметки\n"
        "• Похвала — поддерживающие слова\n\n"
        "Выбери режим или просто напиши.",
        reply_markup=base_kb()
    )

@dp.message(F.text.lower() == "компаньон")
async def mode_companion(m: Message):
    await ensure_user(m.from_user.id)
    await set_mode(m.from_user.id, "companion")
    await set_pending(m.from_user.id, None)
    await m.answer("Режим Компаньон. Пиши, что на душе.", reply_markup=base_kb())

@dp.message(F.text.lower() == "дисциплина")
async def mode_discipline(m: Message):
    await ensure_user(m.from_user.id)
    await set_mode(m.from_user.id, "discipline")
    await set_pending(m.from_user.id, None)
    await m.answer("Режим Дисциплина. Добавляй цели текстом или через кнопки ниже.", reply_markup=discipline_kb())

@dp.message(F.text.lower() == "похвала")
async def mode_praise(m: Message):
    await ensure_user(m.from_user.id)
    await set_mode(m.from_user.id, "praise")
    await set_pending(m.from_user.id, None)
    txt = await praise_for(m.from_user.id)
    await m.answer("Режим Похвала.\n" + txt, reply_markup=base_kb())

@dp.message(F.text.lower() == "помощь")
@dp.message(Command("help"))
async def help_cmd(m: Message):
    await m.answer(
        "Пиши обычным текстом. В Дисциплине можно просто сформулировать цель со сроком, например:\n"
        "«Выучить 20 слов до пятницы», «Сдать отчёт завтра», «Пробежать 3 км 12.09».\n"
        "Кнопки помогут: Добавить цель, Мои цели, Отметить выполненной, Итог дня."
    )

@dp.message(F.text.lower() == "итог дня")
@dp.message(Command("summary"))
async def summary_cmd(m: Message):
    txt = await diary_summary(m.from_user.id)
    await m.answer(txt)

# Кнопки Дисциплины
@dp.message(F.text.lower() == "добавить цель")
async def add_goal_start(m: Message):
    await set_mode(m.from_user.id, "discipline")
    await set_pending(m.from_user.id, "add_goal")
    await m.answer("Сформулируй цель одним сообщением. Можно указать срок: «до пятницы», «завтра», «12.09».", reply_markup=discipline_kb())

@dp.message(F.text.lower() == "мои цели")
async def goals_list(m: Message):
    await set_mode(m.from_user.id, "discipline")
    rows = await list_goals(m.from_user.id, include_done=False)
    await m.answer(format_goals(rows) or "Целей пока нет.", reply_markup=discipline_kb())

@dp.message(F.text.lower() == "отметить выполненной")
async def done_choose(m: Message):
    await set_mode(m.from_user.id, "discipline")
    rows = await list_goals(m.from_user.id, include_done=False)
    if not rows:
        await m.answer("Нет активных целей.", reply_markup=discipline_kb())
        return
    kb = done_inline_kb(rows)
    await set_pending(m.from_user.id, "mark_done")
    await m.answer("Выбери цель, которую выполнил:", reply_markup=discipline_kb())
    await m.answer("Список:", reply_markup=None)
    await bot.send_message(m.chat.id, "Нажми на нужную строку:", reply_markup=kb)

@dp.callback_query(F.data.startswith("done:"))
async def done_cb(c: CallbackQuery):
    try:
        goal_id = int(c.data.split(":")[1])
    except Exception:
        await c.answer("Некорректный выбор", show_alert=True)
        return
    ok = await mark_done(c.from_user.id, goal_id)
    await c.answer("Отмечено" if ok else "Не найдено")
    rows = await list_goals(c.from_user.id, include_done=False)
    await bot.send_message(c.from_user.id, ("Готово. " + ("Осталось:\n" + format_goals(rows) if rows else "Активных целей не осталось.")))

# ================== ОБРАБОТКА СООБЩЕНИЙ БЕЗ КОМАНД ==================
@dp.message(F.text, ~F.text.startswith("/"))
async def route_free_text(m: Message):
    await ensure_user(m.from_user.id)
    mode = await get_mode(m.from_user.id)
    pending = await get_pending(m.from_user.id)

    # Ветка ожидаемого действия
    if pending == "add_goal":
        now_utc3 = datetime.now(timezone.utc) + timedelta(hours=3)
        deadline = parse_deadline(m.text, now_utc3)
        await add_goal(m.from_user.id, m.text, deadline)
        await set_pending(m.from_user.id, None)
        reply = "Цель добавлена"
        if deadline:
            reply += f" (срок {deadline})"
        await m.answer(reply + ".", reply_markup=discipline_kb())
        return

    if pending == "mark_done":
        # Допускаем ввод числом
        if m.text.strip().isdigit():
            ok = await mark_done(m.from_user.id, int(m.text.strip()))
            await set_pending(m.from_user.id, None)
            await m.answer("Отмечено" if ok else "Не нашёл цель с таким ID", reply_markup=discipline_kb())
            return
        # Иначе повторим выбор
        rows = await list_goals(m.from_user.id, include_done=False)
        if not rows:
            await set_pending(m.from_user.id, None)
            await m.answer("Нет активных целей.", reply_markup=discipline_kb())
            return
        await m.answer("Введи номер цели или нажми кнопку ниже.")
        await bot.send_message(m.chat.id, "Список:", reply_markup=done_inline_kb(rows))
        return

    # Свободный ввод по режимам
    if mode == "discipline":
        text = m.text.strip()
        if looks_like_goal(text):
            now_utc3 = datetime.now(timezone.utc) + timedelta(hours=3)
            deadline = parse_deadline(text, now_utc3)
            await add_goal(m.from_user.id, text, deadline)
            msg = "Цель добавлена"
            if deadline:
                msg += f" (срок {deadline})"
            await m.answer(msg + ".", reply_markup=discipline_kb())
        else:
            await m.answer("Принял. Если это цель, добавлю со сроком, если он указан. Посмотреть список — «Мои цели».", reply_markup=discipline_kb())
        return

    if mode == "companion":
        await diary_add(m.from_user.id, m.text)
        await m.answer(reflect_short(m.text), reply_markup=base_kb())
        return

    # praise
    txt = await praise_for(m.from_user.id)
    await m.answer(txt, reply_markup=base_kb())

# ================== ПЛАНИРОВЩИК ==================
scheduler = AsyncIOScheduler()

async def daily_jobs():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id, mode FROM users")
        users = await cur.fetchall()
    for uid, mode in users:
        try:
            if mode == "companion":
                await bot.send_message(uid, "Вечерний Собеседник. Хочешь итог дня? Нажми «Итог дня».")
            elif mode == "discipline":
                await bot.send_message(uid, "Напоминание о целях. Посмотри «Мои цели» и отметь выполненные.")
            else:
                txt = await praise_for(uid)
                await bot.send_message(uid, "Напоминание заботы о себе.\n" + txt)
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
