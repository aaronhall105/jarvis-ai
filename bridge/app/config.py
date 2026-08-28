from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    jarvis_name: str = "Jarvis Core"
    jarvis_environment: str = "development"
    jarvis_log_level: str = "INFO"

    openai_api_key: str = ""
    openai_model: str = "gpt-5-mini"
    jarvis_external_agent_enabled: bool = True
    jarvis_web_search_enabled: bool = True
    jarvis_web_search_model: str = "gpt-5-mini"
    jarvis_connector_health_ttl_seconds: int = 60
    jarvis_connector_timeout_seconds: int = 45
    # Protects setup, receipt, plan and monitor administration endpoints.
    # When empty, those sensitive endpoints remain unavailable rather than open.
    jarvis_integrations_admin_token: str = ""
    jarvis_mobile_voice_token: str = ""
    # Mobile integrations APIs reuse the existing encrypted Android mobile
    # token, but Core maps it to this server-owned principal rather than
    # trusting a user ID supplied by the app or model.
    jarvis_integrations_owner_principal: str = ""
    jarvis_credential_encryption_key: str = ""
    jarvis_google_oauth_client_id: str = ""
    jarvis_google_oauth_client_secret: str = ""
    jarvis_google_oauth_redirect_uri: str = ""
    jarvis_google_android_return_uri: str = "jarvis://integrations/google"
    home_assistant_url: str = "http://homeassistant.local:8123"
    home_assistant_token: str = ""

    # Protects direct memory REST endpoints. Internal AI memory access
    # remains user-scoped and does not use this token.
    jarvis_memory_admin_token: str = ""

    jarvis_admin_mode_enabled: bool = False
    jarvis_admin_confirmation_ttl_seconds: int = 900

    # Separate credential for privileged AI operations.
    # A request body's user_name/user_is_admin fields are identity
    # claims only and must never grant privileged authority.
    jarvis_privileged_admin_token: str = ""

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
