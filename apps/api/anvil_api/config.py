from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://anvil:anvil@localhost:5432/anvil"
    redis_url: str = "redis://localhost:6379/0"
    ml_service_url: str = "http://localhost:8001"

    storage_provider: str = "local"
    storage_bucket: str = "anvil"
    minio_endpoint: str = "http://localhost:9000"
    minio_root_user: str = "anvil"
    minio_root_password: str = "anvilanvil"

    jwt_secret: str = "change-me"
    jwt_algorithm: str = "HS256"
    jwt_expires_minutes: int = 10080

    cors_origins: str = "http://localhost:5173"


settings = Settings()
