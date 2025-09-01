import os
from dataclasses import dataclass

@dataclass
class Settings:
    # fallback токен, если переменная окружения BOT_TOKEN не задана
    fallback_token: str = "8388331432:AAEgbIYU7RypeJckImvNqcwQ9vLXWR4iupw"

    bot_token: str = os.getenv("BOT_TOKEN") or fallback_token
    daily_hour_msk: int = int(os.getenv("DAILY_HOUR_MSK", "20"))
    db_path: str = os.getenv("DB_PATH", "sobesednik.db")

settings = Settings()
