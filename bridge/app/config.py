from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    jarvis_name: str = "Jarvis Core"
    jarvis_environment: str = "development"
    jarvis_log_level: str = "INFO"

    openai_api_key: str = ""
    home_assistant_url: str = "http://homeassistant.local:8123"
    home_assistant_token: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
