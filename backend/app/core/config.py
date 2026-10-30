"""Settings (docs/architecture.md section 8). Environment prefix ``GLDB_``, optional ``.env`` file.

Attribute names are the lower-cased variable name without the prefix. Tests build
``Settings(...)`` explicitly; the application uses the cached ``get_settings()``.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

APP_VERSION = "0.1.0"
IN_MEMORY_DB = ":memory:"

# The owner pays for the Anthropic backend and allows exactly one model on it (docs/anthropic-haiku.md).
# Enforced when settings are validated, when the client is built and again on every call.
ANTHROPIC_HAIKU_MODEL = "claude-haiku-5-5"
ALLOWED_ANTHROPIC_MODELS = frozenset({ANTHROPIC_HAIKU_MODEL})


class SettingsError(ValueError):
    """Fatal start-up misconfiguration (clear message, no secrets)."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="GLDB_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    # storage
    data_dir: Path = Path("./data")
    db_path: str | None = None
    registry_path: Path = Path("./samples/registry.json")
    today: date | None = None

    # LLM
    # "auto": Claude Haiku when GLDB_ANTHROPIC_API_KEY is set, else the OpenAI-compatible server (e.g. the local
    # Qwen) when GLDB_LLM_API_KEY is set, else the fake. GLDB_LLM_BACKEND=openai|anthropic|fake forces one.
    llm_backend: Literal["auto", "fake", "openai", "anthropic"] = "auto"
    llm_base_url: str = "https://openrouter.ai/api/v1"
    llm_api_key: SecretStr = SecretStr("")
    llm_model_cheap: str = ""
    llm_model_strong: str = ""
    llm_timeout_s: float = 60.0
    llm_schema_retries: int = 1
    prices_path: Path | None = None
    # local / self-hosted OpenAI-compatible server knobs (docs/local-llm.md); defaults keep the
    # hosted behaviour unchanged
    llm_enable_thinking: bool = True  # False -> chat_template_kwargs.enable_thinking=false
    llm_supports_vision: bool = True  # False -> the model is text-only: never send image parts
    # Non-empty -> sent as the OpenAI-style ``reasoning_effort`` field. LM Studio ignores
    # chat_template_kwargs.enable_thinking, but honours reasoning_effort="none" (thinking off).
    llm_reasoning_effort: str = ""
    llm_max_concurrency: int = 4  # client-side cap on simultaneous provider calls (all threads)
    llm_max_output_tokens: int = 8000  # raise (16000+) when thinking is on
    llm_retry_503_attempts: int = 2  # retries of a 503 (model loading); the local profile uses 8
    llm_retry_503_backoff_s: float = 1.0  # wait n = backoff * 3**n unless Retry-After is sent
    llm_retry_503_max_wait_s: float = 60.0  # upper bound on any single wait (Retry-After included)
    # Anthropic backend (docs/anthropic-haiku.md): official SDK, Claude Haiku 5.5 only
    anthropic_api_key: SecretStr = SecretStr("")
    anthropic_model: str = (
        ANTHROPIC_HAIKU_MODEL  # anything else is refused (ALLOWED_ANTHROPIC_MODELS)
    )
    anthropic_workspace_id: str = ""  # sent as the anthropic-workspace-id header when non-empty
    anthropic_timeout_s: float = 120.0
    # local OCR for scans (ingest/ocr.py)
    ocr_backend: Literal["auto", "vision", "none"] = "auto"

    # semantic signals (docs/semantic-signals.md): model-derived, evidence-bound checks; OFF by default.
    # "off" -> the checks are not even registered as applicable, every result is byte-identical.
    semantic: Literal["off", "on"] = "off"
    semantic_max_claims: int = 8  # claims kept per model call (more are dropped and counted)

    # modes
    extractor_mode: Literal["text_layer", "llm", "cascade"] = "cascade"
    classifier_mode: Literal["rules", "local", "llm", "cascade"] = "rules"
    classifier_min_prob: float = 0.70
    local_classifier_model: str = ""
    local_classifier_device: Literal["auto", "cpu", "mps", "cuda"] = "auto"
    ubo_threshold_percent: Decimal = Decimal("25.00")

    # rendering
    render_dpi: int = 150
    vision_dpi: int = 150

    # limits and budget guard
    max_upload_mb: int = 25
    max_files_per_case: int = 40
    max_pages_per_case: int = 200
    max_llm_calls_per_case: int = 100
    max_usd_per_case: Decimal = Decimal("2.00")
    # all cases together, per UTC day, paid backends only (anthropic, openai); 0 disables
    max_usd_per_day: Decimal = Decimal("5.00")
    max_page_workers: int = 4
    job_workers: int = 2

    # behaviour
    cache_enabled: bool = True
    money_tolerance: Decimal = Decimal("1.00")
    require_second_approver: bool = True
    retention_days: int = 1825
    pseudonymise_logs: bool = True
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:3000"

    @field_validator("semantic", mode="before")
    @classmethod
    def _semantic_switch(cls, v: Any) -> Any:
        if isinstance(v, bool):
            return "on" if v else "off"
        if isinstance(v, str):
            low = v.strip().lower()
            if low in ("1", "true", "yes", "on"):
                return "on"
            if low in ("", "0", "false", "no", "off"):
                return "off"
        return v

    @field_validator("today", "prices_path", "db_path", mode="before")
    @classmethod
    def _empty_is_none(cls, v: Any) -> Any:
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @model_validator(mode="after")
    def _resolve_anthropic(self) -> Settings:
        """Under the anthropic backend both model roles are the one allowed model and the model has
        vision, whatever a leftover openai profile in the same .env says."""
        if self.effective_llm_backend() == "anthropic":
            self.llm_model_cheap = self.anthropic_model
            self.llm_model_strong = self.anthropic_model
            self.llm_supports_vision = True
        return self

    # ----------------------------------------------------------------------------------
    # derived values
    # ----------------------------------------------------------------------------------

    @property
    def database_path(self) -> str:
        """SQLite location: ``GLDB_DB_PATH`` or ``<DATA_DIR>/gldb.sqlite3`` (``:memory:`` allowed)."""
        if self.db_path:
            return self.db_path
        return str(self.data_dir / "gldb.sqlite3")

    @property
    def files_dir(self) -> Path:
        return self.data_dir / "files"

    @property
    def renders_dir(self) -> Path:
        return self.data_dir / "renders"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def api_key_value(self) -> str:
        """The secret, only for the LLM client. Never log it."""
        return self.llm_api_key.get_secret_value()

    @property
    def anthropic_api_key_value(self) -> str:
        """The paid Anthropic key, only for ``AnthropicLLMClient``. Never log it."""
        return self.anthropic_api_key.get_secret_value()

    def effective_llm_backend(self) -> Literal["fake", "openai", "anthropic"]:
        if self.llm_backend == "auto":
            if self.anthropic_api_key_value.strip():
                return "anthropic"
            return "openai" if self.api_key_value else "fake"
        return self.llm_backend

    def validate_anthropic(self) -> None:
        """The anthropic backend needs a key and exactly the allowed model. Messages never hold the key."""
        problems = []
        if not self.anthropic_api_key_value.strip():
            problems.append("GLDB_ANTHROPIC_API_KEY is empty")
        if self.anthropic_model not in ALLOWED_ANTHROPIC_MODELS:
            problems.append(
                f"GLDB_ANTHROPIC_MODEL must be exactly {ANTHROPIC_HAIKU_MODEL!r} "
                f"(only that model may be used), got {self.anthropic_model!r}"
            )
        if problems:
            raise SettingsError("LLM backend 'anthropic': " + "; ".join(problems))

    def validate_startup(self) -> None:
        """Fail fast on a configuration that cannot work (architecture.md section 10)."""
        if self.effective_llm_backend() == "anthropic":
            self.validate_anthropic()
        if self.effective_llm_backend() == "openai":
            missing = [
                name
                for name, value in (
                    ("GLDB_LLM_API_KEY", self.api_key_value),
                    ("GLDB_LLM_MODEL_CHEAP", self.llm_model_cheap),
                    ("GLDB_LLM_MODEL_STRONG", self.llm_model_strong),
                )
                if not value.strip()
            ]
            if missing:
                raise SettingsError(
                    "LLM backend 'openai' needs these settings, which are empty: "
                    + ", ".join(missing)
                )
        problems = []
        if self.max_pages_per_case <= 0:
            problems.append("GLDB_MAX_PAGES_PER_CASE must be > 0")
        if self.max_llm_calls_per_case <= 0:
            problems.append("GLDB_MAX_LLM_CALLS_PER_CASE must be > 0")
        if self.max_usd_per_case <= 0:
            problems.append("GLDB_MAX_USD_PER_CASE must be > 0")
        if self.max_usd_per_day < 0:
            problems.append("GLDB_MAX_USD_PER_DAY must be >= 0 (0 disables the daily cap)")
        if self.anthropic_timeout_s <= 0:
            problems.append("GLDB_ANTHROPIC_TIMEOUT_S must be > 0")
        if self.job_workers <= 0 or self.max_page_workers <= 0:
            problems.append("GLDB_JOB_WORKERS and GLDB_MAX_PAGE_WORKERS must be > 0")
        if self.llm_max_concurrency <= 0:
            problems.append("GLDB_LLM_MAX_CONCURRENCY must be > 0")
        if self.llm_max_output_tokens <= 0:
            problems.append("GLDB_LLM_MAX_OUTPUT_TOKENS must be > 0")
        if self.llm_retry_503_attempts < 0 or self.llm_retry_503_backoff_s < 0:
            problems.append("GLDB_LLM_RETRY_503_ATTEMPTS and _BACKOFF_S must be >= 0")
        if self.semantic_max_claims <= 0:
            problems.append("GLDB_SEMANTIC_MAX_CLAIMS must be > 0")
        if not 0 <= self.classifier_min_prob <= 1:
            problems.append("GLDB_CLASSIFIER_MIN_PROB must be between 0 and 1")
        if problems:
            raise SettingsError("; ".join(problems))

    def result_settings(self) -> dict[str, str]:
        """Every setting that changes analysis results (hashed into provenance).

        The local-model switches are listed only when they differ from their defaults, so the
        provenance hash of an unchanged hosted configuration does not move."""
        out = {
            "money_tolerance": str(self.money_tolerance),
            "classifier_min_prob": str(self.classifier_min_prob),
            "classifier_mode": self.classifier_mode,
            "local_classifier_model": self.local_classifier_model,
            "extractor_mode": self.extractor_mode,
            "ubo_threshold_percent": str(self.ubo_threshold_percent),
            "llm_model_cheap": self.llm_model_cheap,
            "llm_model_strong": self.llm_model_strong,
            "llm_backend": self.effective_llm_backend(),
            "llm_schema_retries": str(self.llm_schema_retries),
            "max_pages_per_case": str(self.max_pages_per_case),
            "max_llm_calls_per_case": str(self.max_llm_calls_per_case),
            "max_usd_per_case": str(self.max_usd_per_case),
            "vision_dpi": str(self.vision_dpi),
        }
        if not self.llm_enable_thinking:
            out["llm_enable_thinking"] = "False"
        if self.semantic != "off":  # listed only when on: the hash of every other run is unchanged
            out["semantic"] = self.semantic
            out["semantic_max_claims"] = str(self.semantic_max_claims)
        if self.llm_reasoning_effort:
            out["llm_reasoning_effort"] = self.llm_reasoning_effort
        if not self.llm_supports_vision:
            out["llm_supports_vision"] = "False"
        if self.ocr_backend != "auto":
            out["ocr_backend"] = self.ocr_backend
        if self.effective_llm_backend() == "anthropic":
            out["anthropic_model"] = self.anthropic_model
        if self.max_usd_per_day != Decimal("5.00"):
            out["max_usd_per_day"] = str(self.max_usd_per_day)
        return out


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
