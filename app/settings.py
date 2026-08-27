from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    environment: str = "local-staging"
    log_level: str = "INFO"
    log_json: bool = True
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen3.5:4b"
    embedding_model: str = "qwen3-embedding:0.6b"
    embedding_dimension: int = Field(default=1024, ge=1, le=65536)
    provider_timeout_seconds: float = Field(default=180, ge=1, le=900)
    provider_failure_threshold: int = Field(default=3, ge=1, le=20)
    provider_recovery_seconds: int = Field(default=60, ge=5, le=3600)
    codex_enabled: bool = False
    temporal_address: str = "127.0.0.1:7233"
    temporal_namespace: str = "local-orchestra"
    temporal_task_queue: str = "local-ai"
    workflow_execution_timeout_seconds: int = Field(default=2400, ge=60, le=86400)
    workflow_run_timeout_seconds: int = Field(default=2400, ge=60, le=43200)
    postgres_host: str = "127.0.0.1"
    postgres_port: int = Field(default=5432, ge=1, le=65535)
    postgres_database: str = "local_orchestra"
    postgres_user: str = "orchestra_app"
    postgres_password_file: Path = BASE_DIR / ".secrets/postgres_password"
    api_auth_enabled: bool = True
    api_token_file: Path = BASE_DIR / ".secrets/api_token"
    api_rate_limit_requests: int = Field(default=60, ge=1, le=10000)
    api_rate_limit_window_seconds: int = Field(default=60, ge=1, le=3600)
    api_max_response_bytes: int = Field(default=256 * 1024, ge=4096, le=4 * 1024 * 1024)
    api_max_list_limit: int = Field(default=100, ge=1, le=500)
    health_cache_seconds: int = Field(default=15, ge=1, le=300)
    workspace_root: Path = Path("/srv/local-orchestra/workspaces")
    workspace_retention_hours: int = Field(default=168, ge=1, le=8760)
    sandbox_launcher: Path = Path("/usr/local/sbin/orchestra-python-test")
    sandbox_outer_timeout_seconds: int = Field(default=45, ge=5, le=300)
    sandbox_max_output_bytes: int = Field(default=128 * 1024, ge=4096, le=1024 * 1024)
    obsidian_enabled: bool = False
    obsidian_vault_path: Path = Path("/home/derex/Capsulas/Obsidian/Orchestra-Vault")
    obsidian_max_note_bytes: int = Field(default=256 * 1024, ge=1024, le=1024 * 1024)
    obsidian_max_report_bytes: int = Field(default=256 * 1024, ge=4096, le=1024 * 1024)
    obsidian_max_search_notes: int = Field(default=500, ge=1, le=5000)
    obsidian_max_search_results: int = Field(default=20, ge=1, le=100)
    obsidian_context_max_bytes: int = Field(default=32 * 1024, ge=1024, le=256 * 1024)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="ORCHESTRA_",
        extra="forbid",
        validate_default=True,
    )

    @field_validator("environment", "temporal_namespace", "temporal_task_queue")
    @classmethod
    def validate_nonempty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("La configuración no puede estar vacía")
        return value

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        normalized = value.upper()
        if normalized not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("Nivel de logging inválido")
        return normalized

    @field_validator("ollama_base_url")
    @classmethod
    def validate_local_ollama(cls, value: str) -> str:
        normalized = value.rstrip("/")
        if not normalized.startswith(("http://127.0.0.1:", "http://localhost:")):
            raise ValueError("Ollama debe usar una dirección HTTP local")
        return normalized

    @field_validator("obsidian_vault_path")
    @classmethod
    def validate_obsidian_vault_path(cls, value: Path) -> Path:
        if not value.is_absolute() or ".." in value.parts or value == Path("/"):
            raise ValueError("La ruta del vault Obsidian debe ser absoluta, normalizada y específica")
        return value

    @model_validator(mode="after")
    def validate_timeouts(self) -> "Settings":
        if self.workflow_run_timeout_seconds > self.workflow_execution_timeout_seconds:
            raise ValueError("El timeout de run no puede superar el de ejecución")
        return self

    @property
    def postgres_dsn(self) -> str:
        password = self.postgres_password_file.read_text(encoding="utf-8").strip()
        if not password:
            raise RuntimeError("El archivo de contraseña PostgreSQL está vacío")
        return (
            f"postgresql://{self.postgres_user}:{quote(password, safe='')}"
            f"@{self.postgres_host}:{self.postgres_port}"
            f"/{self.postgres_database}?sslmode=disable"
        )


settings = Settings()
