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


LOCAL_OCR_BACKENDS = ("vision", "tesseract", "rapidocr")


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
    # Application-level encryption at rest (docs/encryption-at-rest.md). "on" encrypts every new
    # write (files, page renders, case bodies, review entries, response cache) with AES-256-GCM and
    # needs a key; start-up fails without one. Keys: "id:base64(32 bytes)", comma separated, the
    # FIRST is the active one, all can decrypt (rotation). The key file must be mode 0600 and
    # outside the data directory. Reading works with keys configured whatever the mode.
    encrypt_at_rest: Literal["off", "on"] = "off"
    encryption_keys: SecretStr = SecretStr("")
    encryption_key_file: str = ""  # a path; empty = none
    # Ask Singapore's open ACRA dataset about an applicant UEN that the registry file does not hold
    # (app/registry_live.py). Off by default: it makes a network call per new UEN.
    registry_live: bool = False
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
    ocr_backend: Literal["auto", "vision", "tesseract", "rapidocr", "none"] = "auto"
    # Find the page in a phone photo and straighten it before a local OCR reads it (ingest/flatten.py).
    # Off by default: it cut word misses on simulated photos from 15.6% to 5.3% but did not reduce
    # false alarms end to end (docs/limitations.md section 12). Only the text reading uses the straightened picture; the stored page and the image forensics do not.
    ocr_straighten: bool = False
    # A second, different OCR reads every photographed or scanned page on this machine (macOS Vision,
    # Tesseract or RapidOCR, whichever the first reader is not). A figure the two readers print
    # differently is marked unverified, and a check that fails only on such a figure is lowered to
    # "reading unreliable, check by eye" (ingest/ocr.py, extract/agreement.py, reading_agreement.py).
    # Agreement never lowers anything. Off by default: see docs/limitations.md section 13.
    multi_reader: bool = False
    # With a local OCR chosen under the anthropic backend the model reads OCR text only. True also
    # lets a scanned page whose arithmetic does not add up be re-read once from its image (the one
    # place the model sees pixels). Empty = on in that setup and off elsewhere; false turns it off.
    llm_vision_fallback: bool | None = None
    # A flag raised on scanned pages must be reproduced by a second, different reading of the same
    # pages (Tesseract text <-> the model reading page images) before it counts. Empty = on under the
    # anthropic backend; false keeps every first-reading flag as it was.
    verify_scan_flags: bool | None = None

    # review team (docs/agents.md): a challenger agent argues the innocent reading of judgement flags
    # and a case officer writes the summary. Empty = on under the anthropic backend, off otherwise.
    agents: bool | None = None

    # semantic signals (docs/semantic-signals.md): model-derived, evidence-bound checks. Empty = on
    # whenever a real model is configured (anthropic or openai) and off with the fake backend, where
    # they could only come back "not run". "off" -> the checks are not even registered as
    # applicable, every result is byte-identical.
    semantic: Literal["off", "on"] | None = None
    semantic_max_claims: int = 8  # claims kept per model call (more are dropped and counted)
    # The model-based name-linking check (sem_entity_link) has its own switch, off by default: it
    # fails the fairness invariant in docs/semantic-signals.md (invariant 6) and stays off until
    # the gap is closed and the bank agrees. It needs ``semantic`` to be on as well; ``semantic``
    # on does not turn it on.
    semantic_entity: Literal["off", "on"] = "off"

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
    # purge expired closed cases on a schedule instead of only when someone calls the purge endpoint
    retention_auto: bool = False
    retention_interval_hours: int = 24
    # monitoring (docs/ai-incidents.md): the named person accountable for the AI system and the thresholds
    # GET /monitoring compares the last window of cases against
    ai_owner: str = ""
    monitor_window_days: int = 7
    monitor_min_cases: int = 10
    monitor_partial_rate_max: float = 0.30
    monitor_check_error_rate_max: float = 0.05
    monitor_agent_failure_rate_max: float = 0.10
    monitor_daily_spend_ratio_max: float = 0.80
    monitor_rule_drift_max: float = 0.25
    # Alert delivery, acknowledgement and the silence check (docs/ai-incidents.md). Every sink is optional
    # and off until configured. Secrets are SecretStr: never logged, never returned by any endpoint.
    # 0 = nothing evaluates by itself (run backend/scripts/monitor_once.py from the bank's scheduler instead).
    monitor_interval_minutes: int = 0
    # False: the interval only declares how often an external scheduler (cron) runs monitor_once.py,
    # so the silence check still works but this process starts no thread.
    monitor_in_process: bool = True
    alert_webhook_url: SecretStr = SecretStr("")  # may carry a token, so it is a secret
    alert_webhook_secret: SecretStr = SecretStr("")  # non-empty -> X-Gldb-Signature: sha256=<hmac>
    alert_smtp_host: str = ""
    alert_smtp_port: int = 587
    alert_smtp_starttls: bool = True
    alert_smtp_user: str = ""
    alert_smtp_password: SecretStr = SecretStr("")
    alert_email_from: str = ""
    alert_email_to: str = ""  # comma-separated
    alert_telegram_bot_token: SecretStr = SecretStr("")
    alert_telegram_chat_id: str = ""
    alert_file_path: Path | None = None  # JSON lines, append-only, the last-resort sink
    alert_timeout_s: float = 8.0
    alert_retries: int = 3  # attempts per sink per message
    alert_retry_backoff_s: float = 1.0  # wait n = backoff * 2**n between attempts
    alert_renotify_hours: int = 24  # an open, unacknowledged alert is sent again after this long
    alert_test_max_age_days: int = 30  # a delivered test alert older than this no longer counts
    alert_base_url: str = ""  # shown in messages as the place to read GET /monitoring
    # True when every person signs in with a personal account (GLDB_SITE_USERS on the front): then
    # acknowledging an alert or approving thresholds needs that account, not a typed name.
    personal_accounts: bool = False
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
            if low == "":
                return None
            if low in ("0", "false", "no", "off"):
                return "off"
        return v

    @field_validator("semantic_entity", mode="before")
    @classmethod
    def _semantic_entity_switch(cls, v: Any) -> Any:
        if isinstance(v, bool):
            return "on" if v else "off"
        if isinstance(v, str):
            low = v.strip().lower()
            if low in ("1", "true", "yes", "on"):
                return "on"
            if low in ("", "0", "false", "no", "off"):
                return "off"
        return v

    @field_validator(
        "today",
        "prices_path",
        "db_path",
        "llm_vision_fallback",
        "verify_scan_flags",
        "agents",
        "alert_file_path",
        mode="before",
    )
    @classmethod
    def _empty_is_none(cls, v: Any) -> Any:
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @model_validator(mode="after")
    def _resolve_anthropic(self) -> Settings:
        """Under the anthropic backend both model roles are the one allowed model and the model has
        vision, whatever a leftover openai profile in the same .env says. When a local OCR is
        chosen on purpose (``vision``, ``tesseract``, ``rapidocr``) the model is used as a text-only
        one instead: scans are read by that OCR and no page image is sent (the cost lever)."""
        if self.effective_llm_backend() == "anthropic":
            self.llm_model_cheap = self.anthropic_model
            self.llm_model_strong = self.anthropic_model
            self.llm_supports_vision = self.ocr_backend not in LOCAL_OCR_BACKENDS
            if self.llm_vision_fallback is None:
                self.llm_vision_fallback = not self.llm_supports_vision
        elif self.llm_vision_fallback is None:
            self.llm_vision_fallback = False
        if self.verify_scan_flags is None:
            self.verify_scan_flags = self.effective_llm_backend() == "anthropic"
        if self.agents is None:
            self.agents = self.effective_llm_backend() == "anthropic"
        if self.semantic is None:
            self.semantic = "off" if self.effective_llm_backend() == "fake" else "on"
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
    def encryption_keys_value(self) -> str:
        """The key list, only for ``app.core.crypto``. Never log it."""
        keys = self.encryption_keys  # model_copy(update=...) can leave a plain str here
        return keys if isinstance(keys, str) else keys.get_secret_value()

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

    def validate_encryption(self) -> None:
        """``GLDB_ENCRYPT_AT_REST=on`` needs a usable key; it never falls back to plaintext. A key
        file inside the data directory is refused (the key must not travel with the data)."""
        from app.core.crypto import CryptoConfigError, vault_from_settings

        if self.encryption_key_file.strip():
            key_file = Path(self.encryption_key_file).expanduser().resolve()
            data_dir = Path(self.data_dir).expanduser().resolve()
            if key_file == data_dir or data_dir in key_file.parents:
                raise SettingsError(
                    "GLDB_ENCRYPTION_KEY_FILE is inside GLDB_DATA_DIR: the key must live outside "
                    "the data directory and outside its backups"
                )
        try:
            vault_from_settings(self)
        except CryptoConfigError as exc:
            raise SettingsError(f"encryption at rest: {exc}") from None

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
        self.validate_encryption()
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
        if self.monitor_interval_minutes < 0:
            problems.append("GLDB_MONITOR_INTERVAL_MINUTES must be >= 0 (0 = off)")
        if self.alert_renotify_hours <= 0 or self.alert_test_max_age_days <= 0:
            problems.append(
                "GLDB_ALERT_RENOTIFY_HOURS and GLDB_ALERT_TEST_MAX_AGE_DAYS must be > 0"
            )
        if self.alert_retries <= 0 or self.alert_timeout_s <= 0 or self.alert_retry_backoff_s < 0:
            problems.append(
                "GLDB_ALERT_RETRIES and GLDB_ALERT_TIMEOUT_S must be > 0, GLDB_ALERT_RETRY_BACKOFF_S >= 0"
            )
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
        if self.verify_scan_flags:  # listed only when on: other runs keep their provenance hash
            out["verify_scan_flags"] = "on"
        if self.agents:
            out["agents"] = "on"
        if self.semantic != "off":  # listed only when on: the hash of every other run is unchanged
            out["semantic"] = self.semantic
            out["semantic_max_claims"] = str(self.semantic_max_claims)
            # always stated while semantic is on, so a run with the name-linking check off cannot
            # share a settings hash with an older run in which it ran
            out["semantic_entity"] = self.semantic_entity
        if self.llm_reasoning_effort:
            out["llm_reasoning_effort"] = self.llm_reasoning_effort
        if not self.llm_supports_vision:
            out["llm_supports_vision"] = "False"
        if self.ocr_backend != "auto":
            out["ocr_backend"] = self.ocr_backend
        if self.registry_live:
            out["registry_live"] = "on"
        if self.ocr_straighten:  # listed only when on: other runs keep their provenance hash
            out["ocr_straighten"] = "on"
        if self.multi_reader:
            out["multi_reader"] = "on"
        if self.effective_llm_backend() == "anthropic":
            out["anthropic_model"] = self.anthropic_model
        if self.max_usd_per_day != Decimal("5.00"):
            out["max_usd_per_day"] = str(self.max_usd_per_day)
        return out


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
