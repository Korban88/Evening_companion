import httpx
from datetime import datetime, timezone, timedelta
from typing import List, Tuple, Optional, Literal
from config import settings

Sentiment = Literal["neg", "neu", "pos"]

NEG_MARKERS = [
    "плохо","устал","устала","тяжело","тревога","страх","обидно","грусть",
    "печаль","сложно","один","одиноко","болит","не могу","надоело","выгор"
]
POS_MARKERS = [
    "рад","рада","доволен","довольна","получилось","успех","класс","круто",
    "вышло","продвинулся","сделал","сделала"
]

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

# ---------- Поддержка ----------
def tmpl_support(sent: Sentiment) -> str:
    tod = time_of_day_msk()
    if sent == "neg":
        pool = [
            "Слышу, что непросто. Ты не один.",
            "Можно выдохнуть. Ты сделал достаточно на сегодня.",
            "Не обязательно тянуть всё разом. Шаг за шагом — нормально."
        ]
    elif sent == "pos":
        pool = [
            "Звучит по-тёплому. Сохраним это ощущение.",
            "Классный момент. Пусть это станет опорой.",
            "Радуюсь вместе с тобой."
        ]
    else:
        pool = [
            "Я рядом текстом, но по-настоящему.",
            "Спасибо, что делишься. Это уже забота о себе.",
            "Твоя усталость слышна. Дай себе немного тишины."
        ]
    return pool[hash((sent, tod)) % len(pool)]

# ---------- Мотивация ----------
def tmpl_motivation() -> str:
    tod = time_of_day_msk()
    lines = {
        "утро": [
            "Один простой шаг сейчас задаст тон дню.",
            "Не жди идеальных условий — начни маленьким действием."
        ],
        "день": [
            "Выбери одно короткое действие на 5 минут.",
            "Фокус на одном деле — остальное подождёт."
        ],
        "вечер": [
            "Подведи маленький итог и дай себе отдых.",
            "Сегодня было достаточно. Остальное — позже."
        ],
        "ночь": [
            "Сохрани одну мысль и попробуй отдохнуть.",
            "Лучшее действие сейчас — забота о себе."
        ]
    }
    pool = lines.get(tod, lines["день"])
    return pool[hash(("m", tod)) % len(pool)]

# ---------- Беседа: живая fallback-логика ----------
def _detect_topic(text: str) -> str:
    t = text.lower()
    if any(w in t for w in ["привет","здрав","добрый","ку","hi","hello"]):
        return "greet"
    if any(w in t for w in ["устал","устала","выгор","не могу","надоело"]):
        return "tired"
    if any(w in t for w in ["посор","конфликт","отношен","друг","парн","девуш","семь","муж","жена"]):
        return "relations"
    if any(w in t for w in ["работ","началь","проект","срок","отчёт","учёб","экзам"]):
        return "work"
    if any(w in t for w in ["болит","боль","здоров","сон","бессон","тревог"]):
        return "health"
    if len(t.strip()) <= 8:
        return "short"
    return "generic"

def talk_fallback(user_text: str) -> str:
    sent = detect_sentiment(user_text)
    topic = _detect_topic(user_text)
    if topic == "greet":
        return "Привет. Как тебе этот день?"
    if topic == "short":
        return "Я здесь. Расскажи, что у тебя на душе."
    if topic == "tired":
        return "Слышу усталость. Что сильнее всего выматывает тебя сейчас?"
    if topic == "relations":
        return "Отношения — это важно. Что именно задело больше всего в этой ситуации?"
    if topic == "work":
        return "Рабочие дела могут давить. Что сейчас основная трудность для тебя?"
    if topic == "health":
        return "Тело и сон многое решают. Как ты себя чувствуешь прямо сейчас?"
    if sent == "neg":
        return "Понимаю, что непросто. Что в этом для тебя самое тяжёлое?"
    if sent == "pos":
        return "Звучит радостно. Что особенно порадовало тебя в этом?"
    return "Слышу тебя. Расскажи немного больше: что в этом для тебя главное?"

# ---------- Вызов LLM ----------
async def llm_generate_talk(user_text: str, history_pairs: List[Tuple[str, str]]) -> Optional[str]:
    if settings.llm_provider == "none":
        return None
    sys_prompt = (
        "Ты тёплый, спокойный собеседник. Веди естественный диалог на уровне друга: короткое отражение мысли "
        "и уместный уточняющий вопрос по теме. Без советов, если их не просили. Без планов на завтра. Без эмодзи."
    )
    ctx = "\n".join([f"{'Пользователь' if r=='user' else 'Ассистент'}: {t}" for r, t in history_pairs[-settings.history_max_msgs:]])
    user_prompt = (
        f"Контекст последних сообщений:\n{ctx}\n\n"
        f"Текущее сообщение пользователя: {user_text}\n\n"
        "Ответь 1–2 фразами: короткое эмпатичное отражение + уместный вопрос. Не давай советов, не упоминай «завтра»."
    )
    return await _call_llm(user_prompt, sys_prompt)

async def llm_generate_support(user_text: str) -> Optional[str]:
    if settings.llm_provider == "none":
        return None
    sys_prompt = "Ты поддерживающий ассистент. Дай 1–2 короткие фразы принятия и утешения. Без советов. Без эмодзи."
    user_prompt = f"Пользователь пишет: {user_text}\nСформулируй 1–2 строки поддержки."
    return await _call_llm(user_prompt, sys_prompt)

async def llm_generate_motivation(user_text: Optional[str]) -> Optional[str]:
    if settings.llm_provider == "none":
        return None
    sys_prompt = "Ты мотивирующий ассистент. Дай одну короткую реалистичную фразу-подсказку к действию на сегодня. Без пафоса. Без эмодзи."
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

# ---------- Публичные функции ----------
async def generate_talk_reply(user_text: str, recent_history: List[Tuple[str, str]]) -> str:
    llm = await llm_generate_talk(user_text, recent_history)
    if llm:
        return llm
    return talk_fallback(user_text)

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
