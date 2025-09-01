import asyncio
import httpx
from datetime import datetime, timezone, timedelta
from typing import List, Tuple, Optional, Literal
from config import settings

Sentiment = Literal["neg", "neu", "pos"]

NEG_MARKERS = ["плохо", "устал", "устала", "тяжело", "тревога", "страх", "обидно", "не получилось", "кошмар", "сложно", "один", "одиноко", "больно", "упал", "упала"]
POS_MARKERS = ["рад", "рада", "доволен", "довольна", "получилось", "успех", "кайф", "класс", "круто", "вышло", "продвинулся", "получилось"]

def detect_sentiment(text: str) -> Sentiment:
    low = text.lower()
    neg = any(m in low for m in NEG_MARKERS)
    pos = any(m in low for m in POS_MARKERS)
    if neg and not pos:
        return "neg"
    if pos and not neg:
        return "pos"
    return "neu"

def time_of_day_msk() -> str:
    now = datetime.now(timezone.utc) + timedelta(hours=3)
    h = now.hour
    if 6 <= h < 12:
        return "утро"
    if 12 <= h < 18:
        return "день"
    if 18 <= h < 24:
        return "вечер"
    return "ночь"

# ===== ТЕКСТОВЫЕ ШАБЛОНЫ =====

def tmpl_support(sent: Sentiment) -> str:
    tod = time_of_day_msk()
    base_neg = [
        "Твоим чувствам есть место. Ты не один.",
        "Ты сделал достаточно на сегодня. Можно позволить себе выдохнуть.",
        "Не обязательно тащить всё сразу. Шаг за шагом — нормально."
    ]
    base_neu = [
        "Я рядом текстом, но по-настоящему. Береги себя.",
        "Твоя усталость слышна. Дай себе немного тишины.",
        "Спасибо, что делишься. Это уже забота о себе."
    ]
    base_pos = [
        "Звучит тепло. Сохраним это ощущение.",
        "Отлично, пусть это станет опорой на завтра.",
        "Классный штрих к дню. Заметим и пойдем дальше."
    ]
    if sent == "neg":
        pool = base_neg
    elif sent == "pos":
        pool = base_pos
    else:
        pool = base_neu
    pick = pool[hash((sent, tod)) % len(pool)]
    return f"{pick}"

def tmpl_motivation() -> str:
    tod = time_of_day_msk()
    lines = {
        "утро": [
            "Один простой шаг сейчас задаст тон дню.",
            "Не ищи идеальных условий — начни с малого."
        ],
        "день": [
            "Сделай короткое действие за 5 минут — оно двинет остальное.",
            "Фокус на одном. Остальное подождёт."
        ],
        "вечер": [
            "Подведи маленький итог и выбери один шаг на завтра.",
            "Сегодня было достаточно. Завтра — продолжишь с маленького шага."
        ],
        "ночь": [
            "Сохрани одну мысль на завтра и дай себе отдохнуть.",
            "Лучшее действие сейчас — забота о себе и сон."
        ]
    }
    pool = lines.get(tod, lines["день"])
    return pool[hash(("m", tod)) % len(pool)]

def tmpl_talk_reflect(user_text: str, sent: Sentiment) -> str:
    t = " ".join(user_text.strip().split())
    if len(t) > 180:
        t = t[:180] + "…"
    pre = {
        "neg": "Слышу в этом напряжение. ",
        "neu": "",
        "pos": "Звучит светло. "
    }[sent]
    return f"{pre}Ты написал: «{t}». Что из этого стоит взять с собой на завтра?"

# ===== ХРАНЕНИЕ КОРОТКОЙ ИСТОРИИ В ПАМЯТИ (в БД хранит основной дневник; историю для LLM передаем как контекст) =====
# История хранится/получается в main.py из таблицы diary. Здесь только форматирование
def format_history_for_llm(history: List[Tuple[str, str]]) -> str:
    # history: [(role, text)] с role in {"user","assistant"}
    parts = []
    for role, text in history[-settings.history_max_msgs:]:
        tag = "Пользователь" if role == "user" else "Ассистент"
        parts.append(f"{tag}: {text}")
    return "\n".join(parts)

# ===== LLM ВЫЗОВ =====

async def llm_generate_talk(user_text: str, history_pairs: List[Tuple[str, str]]) -> Optional[str]:
    if settings.llm_provider == "none":
        return None
    sys_prompt = (
        "Ты тёплый, спокойный собеседник. Коротко отражай мысль пользователя, задавай 1 мягкий уточняющий вопрос. "
        "Не давай советов, если не просили. Без эмодзи."
    )
    user_prompt = (
        f"Контекст последних сообщений:\n{format_history_for_llm(history_pairs)}\n\n"
        f"Текущее сообщение пользователя: {user_text}\n\n"
        "Ответь одной-двумя фразами: короткое отражение + один мягкий вопрос."
    )
    return await _call_llm(user_prompt, sys_prompt)

async def llm_generate_support(user_text: str) -> Optional[str]:
    if settings.llm_provider == "none":
        return None
    sys_prompt = (
        "Ты поддерживающий ассистент. Дай 1-2 короткие фразы принятия и утешения. "
        "Без советов, без оценок, без пафоса. Без эмодзи."
    )
    user_prompt = f"Пользователь пишет: {user_text}\nСформулируй 1–2 строки поддержки."
    return await _call_llm(user_prompt, sys_prompt)

async def llm_generate_motivation(user_text: Optional[str]) -> Optional[str]:
    if settings.llm_provider == "none":
        return None
    sys_prompt = (
        "Ты мотивирующий ассистент. Дай одну короткую реалистичную фразу-подсказку к действию на сегодня. Без пафоса. Без эмодзи."
    )
    user_prompt = f"Контекст: {user_text or 'нет'}"
    return await _call_llm(user_prompt, sys_prompt)

async def _call_llm(user_prompt: str, system_prompt: str) -> Optional[str]:
    try:
        timeout = settings.llm_timeout_s
        if settings.llm_provider == "openai" and settings.openai_api_key:
            headers = {"Authorization": f"Bearer {settings.openai_api_key}"}
            json_body = {
                "model": settings.openai_model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.3,
                "max_tokens": 200,
            }
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.post("https://api.openai.com/v1/chat/completions", headers=headers, json=json_body)
                r.raise_for_status()
                data = r.json()
                return data["choices"][0]["message"]["content"].strip()

        if settings.llm_provider == "deepseek" and settings.deepseek_api_key:
            headers = {"Authorization": f"Bearer {settings.deepseek_api_key}"}
            json_body = {
                "model": settings.deepseek_model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.3,
                "max_tokens": 200,
            }
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.post("https://api.deepseek.com/chat/completions", headers=headers, json=json_body)
                r.raise_for_status()
                data = r.json()
                return data["choices"][0]["message"]["content"].strip()
    except Exception:
        return None
    return None

# ===== ПУБЛИЧНЫЕ ФУНКЦИИ ДЛЯ main.py =====

async def generate_talk_reply(user_text: str, recent_history: List[Tuple[str, str]]) -> str:
    sent = detect_sentiment(user_text)
    # попытка LLM
    llm = await llm_generate_talk(user_text, recent_history)
    if llm:
        return llm
    # fallback
    return tmpl_talk_reflect(user_text, sent)

async def generate_support_reply(user_text: str) -> str:
    sent = detect_sentiment(user_text)
    llm = await llm_generate_support(user_text)
    if llm:
        return llm
    return tmpl_support(sent)

async def generate_motivation_reply(user_text: Optional[str]) -> str:
    llm = await llm_generate_motivation(user_text)
    if llm:
        return llm
    return tmpl_motivation()
