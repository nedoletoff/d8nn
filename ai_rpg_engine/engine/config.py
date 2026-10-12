"""Application settings loaded from environment / ``.env``.

This is a small addition to the layout in the spec: ``pydantic-settings`` (see
spec section 1) needs a home, and a dedicated config module keeps ``main.py``
and ``llm_client`` free of duplicated env plumbing.

The settings are read-only with respect to game mechanics. Nothing here is ever
exposed to the LLM.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root = the directory that contains ``main.py`` (parent of ``engine``).
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Runtime configuration. Field names map to UPPER_CASE env variables."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- LLM provider ------------------------------------------------------
    # Generic (preferred): point these at any OpenAI-compatible endpoint
    # (OpenAI, DeepSeek, OpenRouter, Groq, a local Ollama/LM Studio, …).
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_model: str = ""
    # Legacy OpenRouter names (kept for backwards compatibility).
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # --- Models ------------------------------------------------------------
    default_model: str = "deepseek/deepseek-chat"
    intent_model: str = ""
    narrator_model: str = ""

    # --- LLM client behaviour ---------------------------------------------
    llm_timeout: float = 30.0
    llm_retries: int = 3
    offline_mode: Literal["auto", "force", "never"] = "auto"

    # --- Deterministic engine ---------------------------------------------
    # Hidden from the LLM; used to seed the in-game RNG.
    rng_seed: str | None = None
    fudge_dice: bool = False

    # --- Storage / logging -------------------------------------------------
    save_dir: Path = Field(default=Path("saves"))
    data_dir: Path = Field(default=Path("data"))
    log_dir: Path = Field(default=Path("logs"))
    canon_chain_enabled: bool = True
    autosave: bool = True
    #: render emojis in the UI (off by default for a stable, aligned layout)
    emoji: bool = False

    # --- Normalisation ------------------------------------------------------
    @field_validator("rng_seed", mode="before")
    @classmethod
    def _empty_seed_is_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    # --- Derived helpers ----------------------------------------------------
    @property
    def effective_api_key(self) -> str:
        return (self.llm_api_key or self.openrouter_api_key).strip()

    @property
    def effective_base_url(self) -> str:
        return (self.llm_base_url or self.openrouter_base_url).strip()

    @property
    def effective_model(self) -> str:
        return self.llm_model or self.default_model

    @property
    def intent_model_effective(self) -> str:
        return self.intent_model or self.effective_model

    @property
    def narrator_model_effective(self) -> str:
        return self.narrator_model or self.effective_model

    @property
    def has_llm(self) -> bool:
        """True when an API key is present (does not imply reachability)."""
        return bool(self.effective_api_key)

    @property
    def effective_offline(self) -> bool:
        """Whether the narrative layer should run in offline (dry) mode."""
        if self.offline_mode == "never":
            return False
        if self.offline_mode == "force":
            return True
        return not self.has_llm

    # --- Paths --------------------------------------------------------------
    def resolve(self, path: Path) -> Path:
        """Resolve a configured path against the project root."""
        return path if path.is_absolute() else PROJECT_ROOT / path

    @property
    def saves_path(self) -> Path:
        return self.resolve(self.save_dir)

    @property
    def data_path(self) -> Path:
        return self.resolve(self.data_dir)

    @property
    def logs_path(self) -> Path:
        return self.resolve(self.log_dir)

    def ensure_dirs(self) -> None:
        """Create the storage directories if they do not exist yet."""
        for directory in (self.saves_path, self.data_path, self.logs_path):
            directory.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()
