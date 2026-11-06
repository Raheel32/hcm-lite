from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    DATABASE_URL: str
    SECRET_KEY: str
    TIMEZONE: str = "Asia/Karachi"

    # AI assistant: any OpenAI-compatible chat API that supports tool calling.
    # The key lives only in this backend environment, the browser never sees it.
    AI_API_KEY: str = ""
    AI_BASE_URL: str = "https://api.groq.com/openai/v1"
    AI_MODEL: str = ""
    AI_TIMEOUT_SECONDS: float = 15.0
    AI_RATE_LIMIT_PER_HOUR: int = 20  # 0 switches the limit off (never do that in production)

    model_config = SettingsConfigDict(env_file=".env")


settings = Settings()