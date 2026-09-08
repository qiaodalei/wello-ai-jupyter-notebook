from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BACKEND_DIR / "data"
NOTEBOOKS_DIR = ROOT / "notebooks"
SETTINGS_PATH = DATA_DIR / "settings.json"


class EnvSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AIJUPYTER_",
        env_file=str(ROOT / ".env"),
        extra="ignore",
    )

    api_base: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = "gpt-4o"
    host: str = "127.0.0.1"
    port: int = 8000


class AppSettings(BaseModel):
    api_base: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = "gpt-4o"
    auto_execute: bool = True
    max_repair_rounds: int = 5

    def public_dict(self) -> dict:
        masked = self.model_dump()
        if masked.get("api_key"):
            key = masked["api_key"]
            masked["api_key"] = key[:4] + "•" * max(0, len(key) - 8) + key[-4:] if len(key) > 8 else "••••"
            masked["has_api_key"] = True
        else:
            masked["has_api_key"] = False
        return masked


class SettingsUpdate(BaseModel):
    api_base: str | None = None
    api_key: str | None = Field(default=None)
    model: str | None = None
    auto_execute: bool | None = None
    max_repair_rounds: int | None = Field(default=None, ge=1, le=20)


def load_settings() -> AppSettings:
    env = EnvSettings()
    data: dict = {
        "api_base": env.api_base,
        "api_key": env.api_key or _fallback_openai_key(),
        "model": env.model,
        "auto_execute": True,
        "max_repair_rounds": 5,
    }
    if SETTINGS_PATH.exists():
        try:
            saved = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                data.update({k: v for k, v in saved.items() if v is not None})
        except (OSError, json.JSONDecodeError):
            pass
    return AppSettings(**data)


def save_settings(settings: AppSettings) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(
        json.dumps(settings.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _fallback_openai_key() -> str:
    import os

    return os.environ.get("OPENAI_API_KEY", "")
