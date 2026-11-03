"""Private mode (GLDB_PRIVATE_MODE=on, app/core/privacy.py): a hosted configuration is refused, no outbound
call is attempted, and the mode is reported in GET /monitoring and in the case provenance. No real network:
every HTTP layer that could carry a call is patched to fail the test if it is reached."""

from __future__ import annotations

import socket
import threading
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

import app.main as main
from app import alerts as alerts_mod
from app.core import privacy
from app.core.config import Settings, SettingsError
from app.core.llm import OpenAICompatClient
from app.core.privacy import PrivateModeViolation, host_allowed
from tests.e2e._support import build_bundles, run_bundle, temp_root

LOCAL = dict(
    llm_backend="openai",
    llm_base_url="http://127.0.0.1:1234/v1",
    llm_api_key="local",
    llm_model_cheap="qwen3.8-27b",
    llm_model_strong="qwen3.8-27b",
)


def mk(tmp_path: Path | None = None, **kw: Any) -> Settings:
    base: dict[str, Any] = {"_env_file": None, "private_mode": "on"}
    if tmp_path is not None:
        base.update(data_dir=tmp_path / "data", db_path=str(tmp_path / "data" / "gldb.sqlite3"))
    base.update(kw)
    return Settings(**base)


@pytest.fixture(autouse=True)
def _no_guard_left_behind():
    yield
    privacy.remove_network_guard()


# ------------------------------------------------------------------------------ what counts as private


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1", "127.1.2.3", "::1", "localhost", "10.1.2.3", "172.16.0.9", "192.168.1.20",
        "100.64.0.1", "100.101.102.103", "169.254.1.1", "fd00::1", "llm-host", "mac.local",
        "mac.tail1234.ts.net", "[::1]", "LOCALHOST",
    ],
)  # fmt: skip
def test_private_hosts_are_allowed(host: str) -> None:
    assert host_allowed(host)


@pytest.mark.parametrize(
    "host",
    [
        "openrouter.ai", "api.anthropic.com", "api.telegram.org", "data.gov.sg", "8.8.8.8",
        "93.184.216.34", "100.128.0.1", "172.32.0.1", "192.169.0.1", "2606:4700::1111", "", None,
    ],
)  # fmt: skip
def test_public_hosts_are_refused(host: str | None) -> None:
    assert not host_allowed(host)


def test_an_explicitly_allowed_host_passes() -> None:
    assert not host_allowed("llm.bank.example")
    assert host_allowed("llm.bank.example", ["llm.bank.example"])
    assert host_allowed("LLM.bank.example.", ["llm.bank.example"])


# ------------------------------------------------------------------------------ start-up refusals


def test_default_is_off_and_changes_nothing() -> None:
    s = Settings(_env_file=None)
    assert s.private_mode == "off" and "private_mode" not in s.result_settings()
    s.validate_startup()


def test_hosted_anthropic_backend_is_refused() -> None:
    s = mk(llm_backend="anthropic", anthropic_api_key="sk-ant-x")
    with pytest.raises(SettingsError, match="hosted model"):
        s.validate_startup()


def test_an_anthropic_key_alone_is_refused_even_with_the_fake_backend() -> None:
    s = mk(llm_backend="fake", anthropic_api_key="sk-ant-x")
    with pytest.raises(SettingsError, match="hosted model") as err:
        s.validate_startup()
    assert "sk-ant-x" not in str(err.value)  # the message never holds a key


def test_auto_backend_with_an_anthropic_key_is_refused() -> None:
    with pytest.raises(SettingsError, match="hosted model"):
        mk(anthropic_api_key="sk-ant-x").validate_startup()


@pytest.mark.parametrize(
    "url",
    [
        "https://openrouter.ai/api/v1",
        "https://api.openai.com/v1",
        "http://8.8.8.8:8080/v1",
        "http://gateway.example.com:8080/v1",
    ],
)
def test_an_openai_compatible_hosted_url_is_refused(url: str) -> None:
    s = mk(**{**LOCAL, "llm_base_url": url})
    with pytest.raises(SettingsError, match="GLDB_LLM_BASE_URL"):
        s.validate_startup()


def test_the_default_openrouter_url_with_a_key_is_refused() -> None:
    with pytest.raises(SettingsError, match="OpenRouter"):
        mk(llm_api_key="k", llm_model_cheap="m", llm_model_strong="m").validate_startup()


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:1234/v1",
        "http://localhost:8080/v1",
        "http://100.64.0.1:8080/v1",
        "http://192.168.1.5:8080/v1",
        "http://llm-host:8080/v1",
    ],
)
def test_loopback_private_and_tailnet_urls_are_accepted(url: str) -> None:
    mk(**{**LOCAL, "llm_base_url": url}).validate_startup()


def test_an_allowed_host_is_accepted_only_when_listed() -> None:
    url = "https://llm.bank.example/v1"
    with pytest.raises(SettingsError):
        mk(**{**LOCAL, "llm_base_url": url}).validate_startup()
    mk(
        **{**LOCAL, "llm_base_url": url}, private_allowed_hosts="other.example, llm.bank.example"
    ).validate_startup()


def test_the_live_register_lookup_is_refused() -> None:
    with pytest.raises(SettingsError, match="REGISTRY_LIVE"):
        mk(llm_backend="fake", registry_live=True).validate_startup()


def test_telegram_and_external_alert_sinks_are_refused() -> None:
    with pytest.raises(SettingsError, match="TELEGRAM"):
        mk(
            llm_backend="fake", alert_telegram_bot_token="1:T", alert_telegram_chat_id="1"
        ).validate_startup()
    with pytest.raises(SettingsError, match="WEBHOOK"):
        mk(
            llm_backend="fake", alert_webhook_url="https://hooks.slack.com/services/X"
        ).validate_startup()
    with pytest.raises(SettingsError, match="SMTP"):
        mk(llm_backend="fake", alert_smtp_host="smtp.gmail.com").validate_startup()
    mk(
        llm_backend="fake",
        alert_webhook_url="http://10.0.0.5:9000/hook",
        alert_smtp_host="mail.internal",
    ).validate_startup()


def test_the_app_refuses_to_start_with_a_hosted_configuration(tmp_path: Path) -> None:
    s = mk(tmp_path, llm_backend="anthropic", anthropic_api_key="sk-ant-x")
    with pytest.raises(SettingsError):
        main.create_app(s)
    assert not privacy.network_guard_installed()


def test_several_problems_are_listed_together() -> None:
    s = mk(llm_backend="anthropic", anthropic_api_key="k", registry_live=True)
    with pytest.raises(SettingsError) as err:
        s.validate_startup()
    assert "hosted model" in str(err.value) and "REGISTRY_LIVE" in str(err.value)


# ------------------------------------------------------------------------------ call sites


def test_the_openai_client_refuses_a_hosted_url_in_private_mode() -> None:
    s = mk(**{**LOCAL, "llm_base_url": "https://openrouter.ai/api/v1"})
    with pytest.raises(PrivateModeViolation):
        OpenAICompatClient(s, sdk=object())
    OpenAICompatClient(mk(**LOCAL), sdk=object())  # loopback is fine


def test_the_anthropic_client_is_never_built_in_private_mode() -> None:
    from app.core.llm import get_llm_client

    s = mk(llm_backend="anthropic", anthropic_api_key="sk-ant-x")
    with pytest.raises(PrivateModeViolation):
        get_llm_client(s)


def test_alert_sinks_outside_the_network_are_dropped_in_private_mode() -> None:
    s = mk(
        llm_backend="fake",
        alert_webhook_url="https://hooks.slack.com/services/X",
        alert_telegram_bot_token="1:T",
        alert_telegram_chat_id="1",
        alert_smtp_host="smtp.gmail.com",
        alert_email_from="a@b.c",
        alert_email_to="d@e.f",
    )
    assert alerts_mod.build_sinks(s) == []
    ok = mk(
        llm_backend="fake",
        alert_webhook_url="http://10.0.0.5:9000/hook",
        alert_smtp_host="mail.internal",
        alert_email_from="a@b.c",
        alert_email_to="d@e.f",
    )
    assert [x.name for x in alerts_mod.build_sinks(ok)] == ["webhook", "email"]
    off = Settings(
        _env_file=None,
        llm_backend="fake",
        alert_telegram_bot_token="1:T",
        alert_telegram_chat_id="1",
    )
    assert [x.name for x in alerts_mod.build_sinks(off)] == ["telegram"]  # unchanged when off


# ------------------------------------------------------------------------------ no outbound call


def _forbid_http(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Patch every layer an HTTP call could use; record the attempt and fail it."""
    seen: list[str] = []

    def boom(*a: Any, **k: Any) -> Any:
        seen.append(repr(a[1:2]))
        raise AssertionError("an outbound HTTP call was attempted")

    monkeypatch.setattr(httpx.Client, "send", boom)
    monkeypatch.setattr(httpx.Client, "request", boom)
    monkeypatch.setattr(httpx.Client, "get", boom)
    monkeypatch.setattr(httpx.Client, "post", boom)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", boom)
    return seen


def test_the_live_register_makes_no_call_in_private_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from app.core.context import CaseContext  # noqa: F401
    from app.pipeline import attach_live_entity
    from app.registry_live import AcraLookup, RegistryUnavailable

    seen = _forbid_http(monkeypatch)
    # a configuration that start-up would have refused: the call sites must still hold
    s = mk(tmp_path, llm_backend="fake", registry_live=True)

    class Ctx:
        settings = s
        registry = None
        sources: dict = {}  # noqa: RUF012 - what the verification sources record per source

        class case:  # noqa: N801
            class applicant:  # noqa: N801
                uen = "200000001A"
                name = "Acme Pte Ltd"

        today = None

    attach_live_entity(Ctx())  # type: ignore[arg-type]
    assert Ctx.registry is None and seen == []
    assert Ctx.sources["acra"].status == "blocked"  # skipped on purpose: outside coverage
    with pytest.raises(RegistryUnavailable, match="private mode"):
        AcraLookup(tmp_path / "rc", private=True).lookup("200000001A")
    assert seen == []


def test_the_socket_guard_blocks_connections_and_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    resolved: list[str] = []
    real = socket.getaddrinfo

    def spy(host: Any, *a: Any, **k: Any) -> Any:
        resolved.append(str(host))
        return real(host, *a, **k)

    monkeypatch.setattr(
        socket, "getaddrinfo", spy
    )  # the guard wraps this, so DNS would show up here
    privacy.install_network_guard([])
    with pytest.raises(PrivateModeViolation):
        socket.create_connection(("93.184.216.34", 80), timeout=1)
    with pytest.raises(PrivateModeViolation):
        socket.getaddrinfo("example.com", 443)
    with pytest.raises(PrivateModeViolation):
        socket.socket().connect(("8.8.8.8", 53))
    with pytest.raises(PrivateModeViolation):
        httpx.get("https://api.anthropic.com/v1/messages", timeout=1)
    assert resolved == []  # no name was ever sent to DNS


def test_the_socket_guard_lets_loopback_and_unix_sockets_through() -> None:
    import tempfile

    privacy.install_network_guard([])
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    got: list[bytes] = []

    def serve() -> None:
        conn, _ = server.accept()
        got.append(conn.recv(4))
        conn.close()

    t = threading.Thread(target=serve)
    t.start()
    with socket.create_connection(("127.0.0.1", port), timeout=2) as c:
        c.sendall(b"ping")
    t.join(2)
    server.close()
    assert got == [b"ping"]
    path = tempfile.mkdtemp(prefix="gp-") + "/s"
    us = socket.socket(socket.AF_UNIX)
    us.bind(path)
    us.listen(1)
    cl = socket.socket(socket.AF_UNIX)
    cl.connect(path)
    cl.close()
    us.close()


def test_a_name_that_resolves_is_allowed_when_listed(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake(host: Any, *a: Any, **k: Any) -> Any:
        calls.append(str(host))
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.9", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    privacy.install_network_guard(["llm.bank.example"])
    with pytest.raises(PrivateModeViolation):
        socket.getaddrinfo("other.example", 80)
    assert socket.getaddrinfo("llm.bank.example", 80)
    assert calls == ["llm.bank.example"]
    # the address the allowed name resolved to may be connected to (the connect itself is not made here)
    probe = socket.socket()
    probe.settimeout(0.01)
    with pytest.raises(
        (OSError, AssertionError)
    ) as err:  # AssertionError: the suite's own network ban
        probe.connect(("203.0.113.9", 9))
    probe.close()
    assert not isinstance(err.value, PrivateModeViolation)


def test_the_app_installs_the_guard_and_reports_private_mode(tmp_path: Path) -> None:
    s = mk(tmp_path, llm_backend="fake", private_allowed_hosts="llm.bank.example")
    application = main.create_app(s)
    assert privacy.network_guard_installed()
    client = TestClient(application)
    body = client.get("/monitoring").json()
    assert body["private_mode"] == "on"
    assert body["allowed_hosts"] == ["llm.bank.example"]
    assert body["outbound_lookups"] == "disabled" and body["network_guard"] == "installed"


def test_monitoring_says_off_by_default(tmp_path: Path) -> None:
    s = Settings(
        _env_file=None,
        llm_backend="fake",
        data_dir=tmp_path / "data",
        db_path=str(tmp_path / "data" / "gldb.sqlite3"),
    )
    body = TestClient(main.create_app(s)).get("/monitoring").json()
    assert body["private_mode"] == "off" and "allowed_hosts" not in body
    assert not privacy.network_guard_installed()


def test_a_whole_case_in_private_mode_attempts_no_outbound_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = temp_root("gldb-private-")
    bundles = build_bundles(root, scenario="kyc", variants=("clean",))
    seen = _forbid_http(monkeypatch)
    connects: list[Any] = []
    privacy.install_network_guard([])
    real_connect = privacy._guard["orig"]["connect"]  # type: ignore[index]

    def spy(self: socket.socket, address: Any) -> Any:
        connects.append(address)
        return real_connect(self, address)

    privacy._guard["orig"]["connect"] = spy  # type: ignore[index]
    settings = mk(root / "case", llm_backend="fake", semantic="off")
    run = run_bundle(bundles["clean"], settings=settings)
    assert seen == [] and connects == []
    prov = run.case.provenance
    assert prov is not None and prov.private_mode == "on"
    assert run.case.model_dump(mode="json")["provenance"]["private_mode"] == "on"


def test_provenance_has_no_private_mode_key_when_off() -> None:
    root = temp_root("gldb-private-off-")
    bundles = build_bundles(root, scenario="kyc", variants=("clean",))
    run = run_bundle(bundles["clean"], tmp=root / "case")
    assert "private_mode" not in run.case.model_dump(mode="json", exclude_none=False)["provenance"]


def test_the_two_private_mode_gates_agree() -> None:
    """core/outbound.py (used by the verification sources) and core/privacy.py (used by the register
    lookup and the socket guard) must never disagree about whether private mode is on."""
    from app.core.outbound import outbound_blocked
    from app.core.privacy import outbound_lookups_enabled

    for mode in ("off", "on"):
        s = mk(private_mode=mode)
        assert (outbound_blocked(s) is None) == outbound_lookups_enabled(s)
