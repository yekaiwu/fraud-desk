from datetime import date
from decimal import Decimal

import pytest

from app.core.config import Settings, SettingsError, get_settings


def make(**kw) -> Settings:  # noqa: ANN003
    return Settings(_env_file=None, **kw)


def test_defaults_match_architecture_table() -> None:
    s = make()
    assert str(s.data_dir) == "data"
    assert s.database_path.endswith("gldb.sqlite3")
    assert str(s.registry_path) == "samples/registry.json"
    assert s.today is None and s.llm_backend == "auto"
    assert s.llm_base_url == "https://openrouter.ai/api/v1"
    assert s.llm_timeout_s == 60 and s.llm_schema_retries == 1
    assert s.extractor_mode == "cascade" and s.classifier_mode == "rules"
    assert s.classifier_min_prob == 0.70
    assert s.ubo_threshold_percent == Decimal("25.00")
    assert (s.render_dpi, s.vision_dpi) == (150, 150)
    assert (s.max_upload_mb, s.max_files_per_case) == (25, 40)
    assert (s.max_pages_per_case, s.max_llm_calls_per_case) == (200, 100)
    assert s.max_usd_per_case == Decimal("2.00")
    assert (s.max_page_workers, s.job_workers) == (4, 2)
    assert s.cache_enabled is True and s.money_tolerance == Decimal("1.00")
    assert s.require_second_approver is True and s.retention_days == 1825
    assert s.pseudonymise_logs is True and s.log_level == "INFO"
    assert s.cors_origin_list == ["http://localhost:3000"]
    assert s.local_classifier_model == "" and s.local_classifier_device == "auto"


def test_env_prefix_and_empty_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GLDB_MONEY_TOLERANCE", "2.50")
    monkeypatch.setenv("GLDB_TODAY", "")
    monkeypatch.setenv("GLDB_PRICES_PATH", "")
    monkeypatch.setenv("GLDB_CORS_ORIGINS", "http://a.example, http://b.example")
    s = make()
    assert s.money_tolerance == Decimal("2.50") and s.today is None and s.prices_path is None
    assert s.cors_origin_list == ["http://a.example", "http://b.example"]
    monkeypatch.setenv("GLDB_TODAY", "2026-10-27")
    assert make().today == date(2026, 10, 27)


def test_db_path_memory_allowed(tmp_path) -> None:  # noqa: ANN001
    assert make(db_path=":memory:").database_path == ":memory:"
    assert make(data_dir=tmp_path).database_path == str(tmp_path / "gldb.sqlite3")


def test_auto_backend_is_fake_without_key() -> None:
    assert make().effective_llm_backend() == "fake"
    assert make(llm_api_key="k").effective_llm_backend() == "openai"
    assert make(llm_backend="fake", llm_api_key="k").effective_llm_backend() == "fake"


def test_openai_backend_without_models_fails_with_clear_message() -> None:
    s = make(llm_backend="openai", llm_api_key="secret-key")
    with pytest.raises(SettingsError) as err:
        s.validate_startup()
    assert "GLDB_LLM_MODEL_CHEAP" in str(err.value) and "GLDB_LLM_MODEL_STRONG" in str(err.value)
    assert "secret-key" not in str(err.value)
    make(
        llm_backend="openai", llm_api_key="k", llm_model_cheap="a", llm_model_strong="b"
    ).validate_startup()
    make().validate_startup()  # fake backend needs nothing


def test_api_key_never_in_repr() -> None:
    s = make(llm_api_key="super-secret-123")
    assert "super-secret-123" not in repr(s)
    assert "super-secret-123" not in str(s)
    assert "super-secret-123" not in s.model_dump_json()
    assert s.api_key_value == "super-secret-123"


def test_budget_limits_must_be_positive() -> None:
    with pytest.raises(SettingsError):
        make(max_llm_calls_per_case=0).validate_startup()
    with pytest.raises(SettingsError):
        make(max_usd_per_case=Decimal("0")).validate_startup()


def test_result_settings_change_with_thresholds() -> None:
    a, b = make().result_settings(), make(money_tolerance=Decimal("0.50")).result_settings()
    assert a != b
    assert "llm_api_key" not in a


def test_get_settings_is_cached() -> None:
    get_settings.cache_clear()
    assert get_settings() is get_settings()
    get_settings.cache_clear()


# ------------------------------------------------------------------------------------ custom rules


@pytest.mark.parametrize("raw", ["on", "ON", "true", "True", "yes", "1", True])
def test_custom_rules_switch_reads_on(raw) -> None:  # noqa: ANN001
    assert make(custom_rules=raw).custom_rules == "on"


@pytest.mark.parametrize("raw", ["off", "OFF", "false", "no", "0", "", "  ", False])
def test_custom_rules_switch_reads_off(raw) -> None:  # noqa: ANN001
    assert make(custom_rules=raw).custom_rules == "off"


def test_custom_rules_default_is_off_with_documented_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    s = make()
    assert s.custom_rules == "off"
    assert (s.custom_rules_max_per_account, s.custom_rules_dry_run_cases) == (50, 200)
    monkeypatch.setenv("GLDB_CUSTOM_RULES", "true")
    monkeypatch.setenv("GLDB_CUSTOM_RULES_MAX_PER_ACCOUNT", "7")
    monkeypatch.setenv("GLDB_CUSTOM_RULES_DRY_RUN_CASES", "30")
    env = make()
    assert (env.custom_rules, env.custom_rules_max_per_account, env.custom_rules_dry_run_cases) == (
        "on",
        7,
        30,
    )


def test_custom_rules_switch_rejects_nonsense() -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        make(custom_rules="maybe")


@pytest.mark.parametrize("name", ["custom_rules_max_per_account", "custom_rules_dry_run_cases"])
def test_custom_rules_limits_must_be_positive(name: str) -> None:
    with pytest.raises(
        SettingsError,
        match="GLDB_CUSTOM_RULES_MAX_PER_ACCOUNT and GLDB_CUSTOM_RULES_DRY_RUN_CASES must be > 0",
    ):
        make(**{name: 0}).validate_startup()
    make(**{name: 1}).validate_startup()


def test_custom_rules_is_listed_in_the_result_settings_only_when_on() -> None:
    assert "custom_rules" not in make().result_settings()
    assert make(custom_rules="on").result_settings()["custom_rules"] == "on"
    # the other results settings are exactly what they were, so no existing settings hash moves
    on, off = make(custom_rules="on").result_settings(), make().result_settings()
    assert {k: v for k, v in on.items() if k != "custom_rules"} == off
