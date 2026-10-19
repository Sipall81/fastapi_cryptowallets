from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent

class Settings(BaseSettings):
    secret_key : str
    encryption_key: str
    database_url: str
    redis_url: str
    rabbitmq_url: str
    coingecko_api_key:str
    coingecko_url:str
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7
    cookie_secure: bool = False
    cookie_samesite: str = "lax"

    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env",case_sensitive=False,extra="ignore")

settings = Settings()
