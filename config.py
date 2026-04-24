from pydantic_settings import BaseSettings
from pydantic import model_validator
from functools import lru_cache


class Settings(BaseSettings):
    # Database — Railway provides DATABASE_URL as postgresql://
    # We auto-derive the asyncpg variant
    database_url: str = "postgresql://happyrobot:happyrobot_secret@db:5432/happyrobot"

    # Security
    api_key: str = "hr-dev-key-2025"

    # FMCSA
    fmcsa_api_key: str = ""
    fmcsa_base_url: str = "https://mobile.fmcsa.dot.gov/qc/services"

    # CORS
    cors_origins: str = "*"

    # App
    environment: str = "development"
    port: int = 8000

    # Negotiation defaults
    floor_rate_pct: float = 0.85  # 85% of loadboard_rate = absolute floor

    @property
    def database_url_async(self) -> str:
        """Convert standard postgres URL to asyncpg variant."""
        url = self.database_url
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+asyncpg://", 1)
        if url.startswith("postgres://"):
            return url.replace("postgres://", "postgresql+asyncpg://", 1)
        return url

    class Config:
        env_file = ".env"


@lru_cache
def get_settings() -> Settings:
    return Settings()
