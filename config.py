import os
from dataclasses import dataclass

@dataclass
class Settings:
    bot_token: str = os.getenv("BOT_TOKEN", "<<<FALLBACK>>>")
    daily_hour_msk: int = int(os.getenv("DAILY_HOUR_MSK", "20"))
    db_path: str = os.getenv("DB_PATH", "sobesednik.db")

settings = Settings()
