from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
    database_url: str = 'postgresql+psycopg://tuition:tuition@localhost:5432/tuition'
    api_key: str
    bot_token: str = ''
    admin_telegram_ids: str = ''
    api_base_url: str = 'http://localhost:8000'
    timezone: str = 'Asia/Tashkent'


@lru_cache
def get_settings():
    settings = Settings()
    if len(settings.api_key) < 24:
        raise ValueError('API_KEY must contain at least 24 characters')
    return settings
