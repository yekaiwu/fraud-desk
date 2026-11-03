"""Private mode (``GLDB_PRIVATE_MODE=on``): no document text, no query and no alert leaves the private network.

Three layers, each enough to stop a hosted call on its own:

1. **Start-up** (``Settings.validate_startup`` calls :func:`validate_private_settings`): the application refuses to
   start when a hosted model is configured (the anthropic backend or any anthropic key; an OpenAI-compatible base
   URL that is not on the private network, an OpenRouter-style one included), when the live company-register lookup
   is on, or when an alert sink points outside (Telegram always does).
2. **Call sites** (:func:`assert_outbound_allowed`): the LLM clients, the live register lookup and the webhook and
   e-mail alert sinks check the destination before they build a request.
3. **Socket guard** (:func:`install_network_guard`): every connection the process opens, by any library, is checked
   against the same rule, and a name that is not allowed is not even sent to DNS. This is what catches a network
   call added later by somebody who did not know about private mode.

A host is allowed when it is a loopback address, a private (RFC 1918 / unique-local), link-local or Tailscale
(100.64.0.0/10) IP literal, ``localhost``, a single-label name (``llm-host``: it can only be resolved by the local
resolver or MagicDNS), a ``.local`` / ``.ts.net`` / ``.internal`` name, or listed in ``GLDB_PRIVATE_ALLOWED_HOSTS``.
Names are judged by their text, never resolved: the application does not do a DNS lookup to decide.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
import threading
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

if TYPE_CHECKING:  # pragma: no cover
    from app.core.config import Settings

log = logging.getLogger("gldb.privacy")

TAILNET = ipaddress.ip_network("100.64.0.0/10")
PRIVATE_SUFFIXES = (".local", ".ts.net", ".internal", ".lan")


class PrivateModeViolation(RuntimeError):
    """Something tried to reach a destination that private mode does not allow."""


def parse_allowed(raw: str) -> list[str]:
    return [h.strip().lower().strip("[]") for h in raw.split(",") if h.strip()]


def _is_private_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return bool(
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or (isinstance(ip, ipaddress.IPv4Address) and ip in TAILNET)
    )


def host_allowed(host: str | None, allowed: Iterable[str] = ()) -> bool:
    """True when ``host`` (a name or an IP literal, no port) is on the private network or explicitly allowed."""
    if not host:
        return False
    h = host.strip().lower().strip("[]").rstrip(".")
    if not h:
        return False
    if h in {a.rstrip(".") for a in allowed}:
        return True
    try:
        ip = ipaddress.ip_address(h.split("%", 1)[0])
    except ValueError:
        return (
            h == "localhost"
            or h.endswith(".localhost")
            or "." not in h
            or h.endswith(PRIVATE_SUFFIXES)
        )
    return _is_private_ip(ip)


def url_host(url: str) -> str | None:
    try:
        return urlsplit(url.strip()).hostname
    except ValueError:
        return None


def url_allowed(url: str, allowed: Iterable[str] = ()) -> bool:
    return host_allowed(url_host(url), allowed)


# --------------------------------------------------------------------------------------
# Layer 1: start-up
# --------------------------------------------------------------------------------------


def validate_private_settings(s: Settings) -> list[str]:
    """Problems that make a private-mode configuration invalid (empty list = fine). Never contains a secret."""
    if s.private_mode != "on":
        return []
    allowed = parse_allowed(s.private_allowed_hosts)
    problems: list[str] = []
    backend = s.effective_llm_backend()
    if backend == "anthropic" or s.anthropic_api_key_value.strip():
        problems.append(
            "a hosted model is configured (the anthropic backend or GLDB_ANTHROPIC_API_KEY); "
            "private mode sends nothing to a hosted model"
        )
    if backend == "openai" and not url_allowed(s.llm_base_url, allowed):
        problems.append(
            f"GLDB_LLM_BASE_URL host {url_host(s.llm_base_url)!r} is not loopback, a private or tailnet "
            "address or listed in GLDB_PRIVATE_ALLOWED_HOSTS (a hosted gateway such as OpenRouter is refused)"
        )
    if s.registry_live:
        problems.append(
            "GLDB_REGISTRY_LIVE=true asks data.gov.sg about each applicant; private mode has no outbound lookups"
        )
    webhook = s.alert_webhook_url.get_secret_value().strip()
    if webhook and not url_allowed(webhook, allowed):
        problems.append("GLDB_ALERT_WEBHOOK_URL points outside the private network")
    if s.alert_smtp_host.strip() and not host_allowed(s.alert_smtp_host, allowed):
        problems.append("GLDB_ALERT_SMTP_HOST is outside the private network")
    if s.alert_telegram_bot_token.get_secret_value().strip():
        problems.append(
            "GLDB_ALERT_TELEGRAM_BOT_TOKEN: Telegram is a hosted service; use the file, e-mail (internal "
            "relay) or webhook (internal receiver) sink"
        )
    return problems


# --------------------------------------------------------------------------------------
# Layer 2: call sites
# --------------------------------------------------------------------------------------


def assert_outbound_allowed(settings: Settings, url_or_host: str, purpose: str) -> None:
    """Raise :class:`PrivateModeViolation` when private mode is on and the destination is not allowed."""
    if settings.private_mode != "on":
        return
    allowed = parse_allowed(settings.private_allowed_hosts)
    host = url_host(url_or_host) if "://" in url_or_host else url_or_host
    if not host_allowed(host, allowed):
        raise PrivateModeViolation(
            f"private mode: {purpose} to {host!r} refused (not loopback, private, tailnet or allowed)"
        )


def outbound_lookups_enabled(settings: Settings) -> bool:
    """False in private mode: the live company-register lookup and anything like it must not run."""
    return settings.private_mode != "on"


# --------------------------------------------------------------------------------------
# Layer 3: the socket guard
# --------------------------------------------------------------------------------------

_guard_lock = threading.Lock()
_guard: dict[str, Any] | None = None


def _addr_host(address: Any) -> str | None:
    """The host of a socket address; ``None`` for a unix socket path (always allowed)."""
    if isinstance(address, tuple) and address:
        return str(address[0])
    return None


def install_network_guard(allowed: Iterable[str] = ()) -> None:
    """Refuse every outbound connection and name lookup that private mode does not allow, process-wide.

    Idempotent; :func:`remove_network_guard` undoes it (tests). Unix sockets and bind/listen are untouched."""
    global _guard
    allowed_list = list(allowed)
    with _guard_lock:
        if _guard is not None:
            _guard["allowed"][:] = allowed_list
            return
        orig = {
            "connect": socket.socket.connect,
            "connect_ex": socket.socket.connect_ex,
            "getaddrinfo": socket.getaddrinfo,
            "create_connection": socket.create_connection,
        }
        # addresses that an allowed NAME resolved to: the connection that follows is to the name
        state: dict[str, Any] = {"orig": orig, "allowed": allowed_list, "approved": set()}

        def check(host: str | None, what: str) -> None:
            if host is None or host == "":
                return
            if host in state["approved"]:
                return
            if not host_allowed(host, state["allowed"]):
                raise PrivateModeViolation(f"private mode: {what} to {host!r} refused")

        def connect(self: socket.socket, address: Any) -> Any:
            check(_addr_host(address), "connection")
            return orig["connect"](self, address)

        def connect_ex(self: socket.socket, address: Any) -> Any:
            check(_addr_host(address), "connection")
            return orig["connect_ex"](self, address)

        def getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
            if isinstance(host, bytes):
                host = host.decode("ascii", "ignore")
            check(host if isinstance(host, str) else None, "name lookup")
            found = orig["getaddrinfo"](host, *args, **kwargs)
            if isinstance(host, str) and host:
                state["approved"].update(str(entry[4][0]) for entry in found if entry[4])
            return found

        def create_connection(address: Any, *args: Any, **kwargs: Any) -> Any:
            check(_addr_host(address), "connection")
            return orig["create_connection"](address, *args, **kwargs)

        socket.socket.connect = connect  # type: ignore[method-assign]
        socket.socket.connect_ex = connect_ex  # type: ignore[method-assign]
        socket.getaddrinfo = getaddrinfo  # type: ignore[assignment]
        socket.create_connection = create_connection  # type: ignore[assignment]
        _guard = state
        log.warning("private mode: outbound network guard installed")


def remove_network_guard() -> None:
    global _guard
    with _guard_lock:
        if _guard is None:
            return
        orig = _guard["orig"]
        socket.socket.connect = orig["connect"]  # type: ignore[method-assign]
        socket.socket.connect_ex = orig["connect_ex"]  # type: ignore[method-assign]
        socket.getaddrinfo = orig["getaddrinfo"]  # type: ignore[assignment]
        socket.create_connection = orig["create_connection"]  # type: ignore[assignment]
        _guard = None


def network_guard_installed() -> bool:
    return _guard is not None


def describe(settings: Settings) -> dict[str, Any]:
    """What GET /monitoring reports."""
    on = settings.private_mode == "on"
    out: dict[str, Any] = {"private_mode": "on" if on else "off"}
    if on:
        out.update(
            llm_backend=settings.effective_llm_backend(),
            llm_host=url_host(settings.llm_base_url)
            if settings.effective_llm_backend() == "openai"
            else None,
            allowed_hosts=parse_allowed(settings.private_allowed_hosts),
            outbound_lookups="disabled",
            network_guard="installed" if network_guard_installed() else "not installed",
        )
    return out
