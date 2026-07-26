from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    jarvis_name: str = "Jarvis Core"
    jarvis_environment: str = "development"
    jarvis_log_level: str = "INFO"

    openai_api_key: str = ""
    openai_model: str = "gpt-5-mini"
    home_assistant_url: str = "http://homeassistant.local:8123"
    home_assistant_token: str = ""

    jarvis_admin_mode_enabled: bool = False
    jarvis_admin_confirmation_ttl_seconds: int = 900

    jarvis_awareness_enabled: bool = True
    jarvis_awareness_retention_days: int = 30
    jarvis_proactive_enabled: bool = False
    jarvis_proactive_min_importance: int = 80
    jarvis_proactive_target: str = "living_room"
    jarvis_proactive_cooldown_seconds: int = 300

    jarvis_self_improvement_enabled: bool = True
    jarvis_self_improvement_auto_prepare: bool = True
    jarvis_self_improvement_repeat_threshold: int = 2
    jarvis_self_improvement_latency_failure_ms: int = 7000
    jarvis_self_improvement_admin_token: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
