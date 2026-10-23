from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DAYCUBE_", env_file=PROJECT_ROOT / ".env", extra="ignore"
    )

    vault_root: Path = PROJECT_ROOT / "vault"
    timezone: str = "America/Toronto"
    cors_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]
    extension_id: str = ""
    # Meaning search: on when fastembed is installed (`uv sync --extra vector`); keywords otherwise.
    vector_search: bool = True
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    anthropic_model: str = ""
    anthropic_api_key: SecretStr = Field(default=SecretStr(""), validation_alias="ANTHROPIC_API_KEY")
    modal_app: str = "daycube"

    @field_validator("vault_root")
    @classmethod
    def absolute_root(cls, value: Path) -> Path:
        return (PROJECT_ROOT / value).resolve()

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        ZoneInfo(value)
        return value
