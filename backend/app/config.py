from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # App
    app_name: str = "AutoEngage"
    app_env: str = "development"
    cors_origins: str = "http://localhost:5173"

    # Database
    database_url: str

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # JWT
    jwt_secret: str
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7

    # Encryption
    fernet_key: str

    # LinkedIn OAuth
    linkedin_client_id: str = ""
    linkedin_client_secret: str = ""
    linkedin_redirect_uri: str = "http://localhost:8000/api/integrations/linkedin/callback"

    # OpenRouter
    openrouter_api_key: str = ""
    openrouter_generation_model: str = "anthropic/claude-sonnet-4-5-20250929"
    openrouter_review_model: str = "anthropic/claude-haiku-4-5-20251001"

    # Cloudflare R2
    r2_account_id: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket_name: str = "autoengage-assets"
    r2_endpoint_url: str = ""

    # Meta (Facebook/Instagram) OAuth
    meta_app_id: str = ""
    meta_app_secret: str = ""
    meta_redirect_uri: str = "http://localhost:8000/api/integrations/meta/callback"

    # WhatsApp Sidecar
    whatsapp_sidecar_url: str = "http://whatsapp-sidecar:3001"

    # Sentry (optional — only set in staging/production)
    sentry_dsn: str = ""

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def log_level(self) -> str:
        return "INFO" if self.is_production else "DEBUG"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",")]


settings = Settings()

# ---------------------------------------------------------------------------
# Application constants (not env-configurable — change in code)
# ---------------------------------------------------------------------------

# Org invites
INVITE_EXPIRY_DAYS = 7

# OAuth token refresh: refresh when this many days before expiry
TOKEN_REFRESH_BUFFER_DAYS = 7

# HTTP timeouts (seconds)
HTTP_TIMEOUT = 15.0  # Graph API, LinkedIn API, etc.
LLM_TIMEOUT = 30.0  # OpenRouter / LLM calls

# Engagement stagger delays (seconds)
LIKE_STAGGER_MIN = 1  # Min seconds between user likes
LIKE_STAGGER_MAX = 5  # Max seconds between user likes
COMMENT_STAGGER_MIN = 60  # Min seconds before a comment (1 min)
COMMENT_STAGGER_MAX = 300  # Max seconds before a comment (5 min)
COMMENT_INTER_USER_DELAY = 60  # Additional seconds per user index
