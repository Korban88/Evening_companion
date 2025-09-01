import os
from dataclasses import dataclass

@dataclass
class Settings:
    # Токен Telegram
    fallback_token: str = "8388331432:AAEgbIYU7RypeJckImvNqcwQ9vLXWR4iupw"
    bot_token: str = os.getenv("BOT_TOKEN") or fallback_token

    # Час напоминаний по МСК
    daily_hour_msk: int = int(os.getenv("DAILY_HOUR_MSK", "20"))
    db_path: str = os.getenv("DB_PATH", "sobesednik.db")

    # LLM-провайдер (none|openai|deepseek)
    llm_provider: str = os.getenv("LLM_PROVIDER", "none").lower()
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    deepseek_api_key: str = os.getenv("DEEPSEEK_API_KEY", "")
    deepseek_model: str = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

    # Ограничения
    llm_timeout_s: int = int(os.getenv("LLM_TIMEOUT_S", "12"))
    history_max_msgs: int = int(os.getenv("HISTORY_MAX_MSGS", "6"))  # сколько последних реплик держать в памяти

settings = Settings()
