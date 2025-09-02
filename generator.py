import asyncio
import random
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
    "вышло","продвинулся","сделал","сделала","получилось закрыть","справился"
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
    random.seed(f"support-{sent}-{tod}-{datetime.now(timezone.utc).minute}")
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
    return random.choice(pool)

# ---------- Мотивация ----------
def tmpl_motivation() -> str:
    tod = time_of_day_msk()
    random.seed(f"mot-{tod}-{datetime.now(timezone.utc).minute}")
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
            "Подведи небольшой итог и дай себе отдых.",
            "Сегодня было достаточно. Остальное — позже."
        ],
        "ночь": [
            "Сохрани одну мысль и попробуй отдохнуть.",
            "Лучшее действие сейчас — забота о себе."
        ]
    }
    return random.choice(lines.get(tod, lines["день"]))

# ---------- Беседа: живая логика с учётом контекста ----------
def _detect_topic(text: str) -> str:
    t = text.lower().strip()
    if any(w in t for w in ["привет","здрав","добрый","ку","hi","hello"]):
        return "greet"
    if any(w in t for w in ["как у тебя","как дела","как сам","как сама"]):
        return "ask_me"
    if any(w in t for w in ["ты кто","кто ты","что ты","кто такой"]):
        return "who"
    if any(w in t for w in ["что понимаешь","что ты понимаешь","что именно понимаешь"]):
        return "ask_clarify"
    if any(w in t for w in ["не ответил","не ответила","ответь на вопрос","ты не ответил"]):
        return "complain_no_answer"
    if any(w in t for w in ["устал","устала","выгор","не могу","надоело"]):
        return "tired"
    if any(w in t for w in ["посор","конфликт","отношен","друг","парн","девуш","семь","муж","жена"]):
        return "relations"
    if any(w in t for w in ["работ","началь","проект","срок","отчёт","учёб","экзам"]):
        return "work"
    if any(w in t for w in ["болит","боль","здоров","сон","бессон","тревог"]):
        return "health"
    if len(t) <= 8:
        return "short"
    return "generic"

def _reflect_from_prev(prev_user: Optional[str]) -> str:
    if not prev_user:
        return "Понял."
    s = detect_sentiment(prev_user)
    if s == "neg":
        return "Понимаю, что было непросто."
    if s == "pos":
        return "Понимаю, что это порадовало тебя."
    return "Понимаю тебя."

def talk_fallback(
    user_text: str,
    prev_user_text: Optional[str],
    last_assistant_text: Optional[str]
) -> str:
    sent = detect_sentiment(user_text)
    topic = _detect_topic(user_text)
    random.seed(f"talk-{topic}-{datetime.now(timezone.utc).minute}")

    if topic == "greet":
        return random.choice([
            "Привет. Как тебе этот день?",
            "Рад слышать. Что сейчас у тебя на душе?"
        ])
    if topic == "ask_me":
        return random.choice([
            "Я в порядке и на связи. Давай лучше про тебя — что важно сейчас?",
            "У меня стабильно. Что у тебя происходит прямо сейчас?"
        ])
    if topic == "who":
        return random.choice([
            "Я «Вечерний Собеседник» — тот, кто слушает и отвечает по-человечески. О чём хочешь поговорить?",
            "Я здесь, чтобы быть рядом словом. Можем обсудить что угодно. С чего начнём?"
        ])
    if topic == "ask_clarify":
        base = _reflect_from_prev(prev_user_text)
        return base + " Если сформулировал расплывчато — уточни, о чём тебе хочется поговорить?"
    if topic == "complain_no_answer":
        base = _reflect_from_prev(prev_user_text)
        return base + " Сори, что ушёл в сторону. Спроси ещё раз — я отвечу прямо."
    if topic == "short":
        return random.choice([
            "Я здесь. Расскажи, что у тебя на душе.",
            "Слушаю. О чём хочешь поговорить?"
        ])
    if topic == "tired":
        return random.choice([
            "Слышу усталость. Что сильнее всего выматывает тебя сейчас?",
            "Похоже, сил маловато. Что больше всего давит?"
        ])
    if topic == "relations":
        return random.choice([
            "Отношения — это важно. Что именно задело больше всего в этой ситуации?",
            "Понимаю, это может ранить. Что хочешь прояснить в этом разговоре?"
        ])
    if topic == "work":
        return random.choice([
            "Рабочие дела умеют давить. Что сейчас основная трудность для тебя?",
            "Похоже на напряжение из-за дел. Что мешает больше всего?"
        ])
    if topic == "health":
        return random.choice([
            "Тело и сон многое решают. Как ты себя чувствуешь прямо сейчас?",
            "Здоровье — первично. Что именно беспокоит больше всего?"
        ])

    if sent == "neg":
        return random.choice([
            "Звучит тяжело. Что в этом для тебя самое сложное?",
            "Понимаю, что непросто. Что хотелось бы изменить в первую очередь?"
        ])
    if sent == "pos":
        return random.choice([
            "Звучит радостно. Что особенно порадовало тебя в этом?",
            "Классно слышать. Что из этого хочется сохранить в жизни чаще?"
        ])
    return random.choice([
        "Слышу тебя. Что в этом для тебя главное?",
        "Понимаю. Расскажи немного подробнее, что тебя в этом волнует?"
    ])

# ---------- Вызов LLM с ретраями ----------
async def llm_generate_talk(user_text: str, history_pairs: List[Tuple[str, str]]) -> Optional[str]:
    if settings.llm_provider == "none":
        return None
    sys_prompt = (
        "Ты тёплый, спокойный собеседник. Веди естественный диалог как друг: краткое эмпатичное отражение "
        "и уместный уточняющий вопрос по теме. Не давай советов без запроса. Не упоминай планы на завтра. Без эмодзи."
    )
    ctx = "\n".join([f"{'Пользователь' if r=='user' else 'Ассистент'}: {t}" for r, t in history_pairs[-settings.history_max_msgs:]])
    user_prompt = (
        f"Контекст последних сообщений:\n{ctx}\n\n"
        f"Текущее сообщение пользователя: {user_text}\n\n"
        "Ответь 1–2 фразами: отражение + уместный вопрос. Коротко, по делу."
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
    attempts = 3
    backoff = 1.0
    for i in range(attempts):
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
                    if r.status_code in (429, 500, 502, 503, 504):
                        raise httpx.HTTPStatusError("rate/5xx", request=r.request, response=r)
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
                    if r.status_code in (429, 500, 502, 503, 504):
                        raise httpx.HTTPStatusError("rate/5xx", request=r.request, response=r)
                    r.raise_for_status()
                    data = r.json()
                    return data["choices"][0]["message"]["content"].strip()
            return None
        except httpx.HTTPStatusError:
            if i < attempts - 1:
                await asyncio.sleep(backoff)
                backoff *= 2
                continue
            return None
        except Exception:
            return None
    return None

# ---------- Публичные функции ----------
async def generate_talk_reply(
    user_text: str,
    recent_history: List[Tuple[str, str]],
    prev_user_text: Optional[str],
    last_assistant_text: Optional[str]
) -> str:
    llm = await llm_generate_talk(user_text, recent_history)
    if llm:
        return llm
    return talk_fallback(user_text, prev_user_text, last_assistant_text)

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
