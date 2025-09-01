from dataclasses import dataclass

@dataclass
class Settings:
    # Твой токен зашит сюда
    bot_token: str = "8388331432:AAEgbIYU7RypeJckImvNqcwQ9vLXWR4iupw"
    # Час по Москве для вечернего напоминания
    daily_hour_msk: int = 20
    # Имя файла БД
    db_path: str = "sobesednik.db"

settings = Settings()
