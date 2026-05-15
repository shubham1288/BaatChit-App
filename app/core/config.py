from functools import lru_cache
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve the .env path relative to this file so it always works regardless of CWD
_ENV_FILE = Path(__file__).resolve().parent.parent.parent / ".env"


class Settings(BaseSettings):
    # Application
    app_name: str = "BaatChit"
    environment: str = "development"

    # Database
    database_url: str = "sqlite:///./chat.db"

    # JWT
    secret_key: str = "change-me-in-production-32chars-minimum"
    refresh_secret_key: str = "change-refresh-secret-in-production"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    refresh_token_expire_days: int = 7

    # Rate limiting
    rate_limit_max_messages: int = 60
    rate_limit_window_seconds: int = 60

    # CORS
    allowed_origins: str = "http://localhost:8000,http://127.0.0.1:8000"

    # Encryption
    message_encryption_key: str = "Jv8kBcBgBPzMNEa1ZwCulmwhl9QHBGoTnGASPv3N_3Q="

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def allowed_origins_list(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",")]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()