from pathlib import Path
from urllib.parse import quote

from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen3.5:4b"
    embedding_model: str = "qwen3-embedding:0.6b"
    embedding_dimension: int = 1024
    temporal_address: str = "127.0.0.1:7233"
    temporal_namespace: str = "local-orchestra"
    temporal_task_queue: str = "local-ai"

    postgres_host: str = "127.0.0.1"
    postgres_port: int = 5432
    postgres_database: str = "local_orchestra"
    postgres_user: str = "orchestra_app"
    postgres_password_file: Path = BASE_DIR / ".secrets/postgres_password"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="ORCHESTRA_",
        extra="ignore",
    )

    @property
    def postgres_dsn(self) -> str:
        password = self.postgres_password_file.read_text(
            encoding="utf-8"
        ).strip()

        return (
            f"postgresql://{self.postgres_user}:{quote(password, safe='')}"
            f"@{self.postgres_host}:{self.postgres_port}"
            f"/{self.postgres_database}?sslmode=disable"
        )


settings = Settings()
