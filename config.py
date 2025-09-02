import os
from dataclasses import dataclass

@dataclass
class Settings:
    # Telegram
    fallback_token: str = "8388331432:AAEgbIYU7RypeJckImvNqcwQ9vLXWR4iupw"
    bot_token: str = os.getenv("BOT_TOKEN") or fallback_token

    # Планировщик
    daily_hour_msk: int = int(os.getenv("DAILY_HOUR_MSK", "20"))
    db_path: str = os.getenv("DB_PATH", "sobesednik.db")

    # LLM-провайдер: none | openai | deepseek
    llm_provider: str = os.getenv("LLM_PROVIDER", "openai").lower()

    # OpenAI
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    openai_org_id: str = os.getenv("OPENAI_ORG_ID", "")      # опционально (если в аккаунте несколько организаций)

    # DeepSeek (опционально)
    deepseek_api_key: str = os.getenv("DEEPSEEK_API_KEY", "")
    deepseek_model: str = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

    # Ограничения и пул запросов к LLM
    llm_timeout_s: int = int(os.getenv("LLM_TIMEOUT_S", "12"))
    history_max_msgs: int = int(os.getenv("HISTORY_MAX_MSGS", "6"))
    llm_max_concurrency: int = int(os.getenv("LLM_MAX_CONCURRENCY", "2"))  # одновременно открытых запросов к GPT

    # Подписка/триал
    admin_id: int = int(os.getenv("ADMIN_ID", "0"))                   # твой Telegram ID
    trial_messages: int = int(os.getenv("TRIAL_MESSAGES", "30"))      # сколько сообщений доступно без подписки
    payment_url: str = os.getenv("PAYMENT_URL", "https://t.me/vechernyisobesednik_bot")  # заглушка, заменишь на оплату

settings = Settings()
