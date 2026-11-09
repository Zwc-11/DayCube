import os
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]
# The desktop app keeps its sources apart from the vault, so it names the .env beside the vault.
ENV_FILE = Path(os.environ.get("DAYCUBE_ENV_FILE") or PROJECT_ROOT / ".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DAYCUBE_", env_file=ENV_FILE, extra="ignore")

    vault_root: Path = PROJECT_ROOT / "vault"
    timezone: str = "America/Toronto"
    cors_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]
    extension_id: str = ""
    # Meaning search: on when fastembed is installed (`uv sync --extra vector`); keywords otherwise.
    vector_search: bool = True
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    anthropic_model: str = ""
    # Language models, each off by default ("": code alone, nothing leaves this machine).
    # "anthropic" uses the Anthropic API (ANTHROPIC_API_KEY, DAYCUBE_ANTHROPIC_MODEL); "local" an
    # OpenAI-compatible server on this PC such as llama-server (local_llm_url, local_llm_model).
    # category_llm labels the apps and sites the category rules do not know (ingest/review/labels.py):
    # each unknown app's or site's name and up to three of its titles, once.
    category_llm: str = ""
    # summary_llm words the end-of-day review (ingest/review/summary.py) from the day's numbers and
    # up to about 150 of its records (titles, lines read, searches, transcript lines), then the
    # review is checked against them; without it, or when its answer fails twice, a template does.
    summary_llm: str = ""
    # Yesterday's review pushed to the phone in the morning (ingest/review/push.py): "", "ntfy" or
    # "telegram". Off by default; what leaves the PC is the headline, three sentences, one change
    # and two items for tomorrow.
    summary_push: str = ""
    push_at: str = "08:00"
    ntfy_url: str = ""
    telegram_token: SecretStr = SecretStr("")
    telegram_chat: str = ""
    # The receiver restarts on this checkout's new code (after a pull) when you are away
    # (services/capture-win/capture/update.py); false leaves it to `python -m capture restart`.
    auto_restart: bool = True
    local_llm_url: str = "http://127.0.0.1:8080/v1"
    local_llm_model: str = "local"
    anthropic_api_key: SecretStr = Field(default=SecretStr(""), validation_alias="ANTHROPIC_API_KEY")
    modal_app: str = "daycube"
    # Google's Photorealistic 3D Tiles in a world's view (docs/world.md): a Maps Platform key with the
    # Map Tiles API on. The viewer streams the tiles with it; nothing of Google's is stored.
    google_maps_key: SecretStr = SecretStr("")

    @field_validator("vault_root")
    @classmethod
    def absolute_root(cls, value: Path) -> Path:
        return (PROJECT_ROOT / value).resolve()

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        ZoneInfo(value)
        return value
