"""Settings added for the local LLM server (docs/local-llm.md): defaults, validation, .env handling."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import Settings, SettingsError

LOCAL_PROFILE = """\
GLDB_LLM_BACKEND=openai
GLDB_LLM_BASE_URL=http://llm-host:8080/v1
GLDB_LLM_API_KEY=sk-llm-abc
GLDB_LLM_MODEL_CHEAP=qwen3.8-27b
GLDB_LLM_MODEL_STRONG=qwen3.8-27b
GLDB_LLM_ENABLE_THINKING=false
GLDB_LLM_SUPPORTS_VISION=false
GLDB_LLM_MAX_CONCURRENCY=1
GLDB_LLM_TIMEOUT_S=300
GLDB_LLM_RETRY_503_ATTEMPTS=8
GLDB_OCR_BACKEND=auto
"""


def test_new_defaults_leave_hosted_behaviour_unchanged() -> None:
    s = Settings(_env_file=None)
    assert s.llm_enable_thinking is True and s.llm_supports_vision is True
    assert s.llm_max_concurrency == 4 and s.llm_max_output_tokens == 8000
    assert s.llm_retry_503_attempts == 2 and s.llm_retry_503_backoff_s == 1.0
    assert s.llm_retry_503_max_wait_s == 60.0 and s.ocr_backend == "auto"
    assert s.llm_timeout_s == 60


def test_provenance_hash_input_is_unchanged_by_default_and_moves_with_local_switches() -> None:
    base = Settings(_env_file=None).result_settings()
    assert not {"llm_enable_thinking", "llm_supports_vision", "ocr_backend"} & set(base)
    local = Settings(
        _env_file=None, llm_enable_thinking=False, llm_supports_vision=False, ocr_backend="vision"
    ).result_settings()
    assert local["llm_enable_thinking"] == "False" and local["llm_supports_vision"] == "False"
    assert local["ocr_backend"] == "vision"


def test_local_profile_loads_from_env_file(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(LOCAL_PROFILE)
    s = Settings(_env_file=env)
    assert s.effective_llm_backend() == "openai" and s.llm_model_cheap == "qwen3.8-27b"
    assert s.llm_enable_thinking is False and s.llm_supports_vision is False
    assert s.llm_max_concurrency == 1 and s.llm_timeout_s == 300
    assert s.llm_retry_503_attempts == 8 and s.api_key_value == "sk-llm-abc"
    s.validate_startup()


def test_unknown_gldb_variables_in_env_do_not_crash_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Other tools (the deploy/access layer) keep their own GLDB_* variables in the same .env."""
    env = tmp_path / ".env"
    env.write_text(
        LOCAL_PROFILE + "GLDB_ACCESS_PASSWORD=secret\nGLDB_SOMETHING_NEW=1\nNEXT_PUBLIC_X=2\n"
    )
    monkeypatch.setenv("GLDB_ALSO_UNKNOWN", "yes")
    s = Settings(_env_file=env)
    assert s.llm_supports_vision is False
    assert not hasattr(s, "access_password") and not hasattr(s, "something_new")


@pytest.mark.parametrize(
    ("kw", "needle"),
    [
        ({"llm_max_concurrency": 0}, "GLDB_LLM_MAX_CONCURRENCY"),
        ({"llm_max_output_tokens": 0}, "GLDB_LLM_MAX_OUTPUT_TOKENS"),
        ({"llm_retry_503_attempts": -1}, "GLDB_LLM_RETRY_503_ATTEMPTS"),
    ],
)
def test_invalid_values_fail_startup_clearly(kw: dict, needle: str) -> None:
    with pytest.raises(SettingsError, match=needle):
        Settings(_env_file=None, **kw).validate_startup()


def test_ocr_backend_only_accepts_the_three_values() -> None:
    for value in ("auto", "vision", "none"):
        assert Settings(_env_file=None, ocr_backend=value).ocr_backend == value
    with pytest.raises(ValueError):
        Settings(_env_file=None, ocr_backend="tesseract")
