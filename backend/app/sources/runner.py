"""Ask the external verification sources once per case, before the rules run (docs/verification-sources.md).

``attach_sources(ctx)`` is called by the pipeline next to ``attach_live_entity``. It fills
``ctx.sources`` with one :class:`~app.sources.common.SourceOutcome` per question; the checks only read it.
A source that is not configured is never asked; one that private mode blocks is not asked either; one that
is configured but cannot be reached becomes ``unavailable`` and a partial reason on the case. None of
these ever becomes a flag.
"""

from __future__ import annotations

import logging
import re
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.context import CaseContext
from app.core.crypto import vault_from_settings
from app.core.outbound import PRIVATE_MODE_CODE, outbound_blocked
from app.sources.common import Deadline, SourceOutcome, SourceUnavailable
from app.sources.sanctions import Screener, SnapshotError, load_snapshot

log = logging.getLogger("gldb.sources")

POSTAL_IN_ADDRESS = re.compile(r"(?<!\d)(\d{6})(?!\d)")
MAX_POSTALS = 3
MAX_GST_LOOKUPS = 10
NOT_CONFIGURED = "not_configured"

# source name -> (setting that switches it on, what to tell a person who wants it)
HOW_TO_ENABLE = {
    "sanctions": "GLDB_SANCTIONS_SCREEN=on and a snapshot from backend/scripts/update_sanctions.py",
    "gleif": "GLDB_GLEIF=on",
    "onemap": "GLDB_ONEMAP_TOKEN, or GLDB_ONEMAP_EMAIL and GLDB_ONEMAP_PASSWORD",
    "iras": "GLDB_IRAS_API_KEY",
}


def configured(settings: Any, source: str) -> bool:
    if source == "sanctions":
        return getattr(settings, "sanctions_screen", "off") == "on"
    if source == "gleif":
        return getattr(settings, "gleif", "off") == "on"
    if source == "onemap":
        token = getattr(settings, "onemap_token", None)
        pw = getattr(settings, "onemap_password", None)
        has_token = bool(token and token.get_secret_value())
        has_login = bool(getattr(settings, "onemap_email", "") and pw and pw.get_secret_value())
        return has_token or has_login
    if source == "iras":
        key = getattr(settings, "iras_api_key", None)
        return bool(key and key.get_secret_value())
    return False


def source_status(settings: Any, source: str) -> tuple[str, str]:
    """``(code, reason)`` of why ``source`` will not be asked, or ``("", "")`` when it may be.
    Code is ``not_configured`` or ``private_mode`` (both outside coverage). The sanctions screen is
    offline against a local file, so private mode does not block it; its update script is blocked."""
    if not configured(settings, source):
        return (
            NOT_CONFIGURED,
            f"Not configured (off by default). To use it set {HOW_TO_ENABLE[source]}.",
        )
    if source != "sanctions":
        reason = outbound_blocked(settings)
        if reason:
            return PRIVATE_MODE_CODE, reason
    return "", ""


# ------------------------------------------------------------------------------ the pieces

_SCREENERS: dict[tuple[str, float], Screener] = {}
_SCREENERS_LOCK = threading.Lock()


def snapshot_path(settings: Any) -> Path:
    given = getattr(settings, "sanctions_snapshot", None)
    return Path(given) if given else Path(settings.data_dir) / "sanctions" / "current.json"


def _sanctions(settings: Any, now: datetime) -> tuple[SourceOutcome, str | None]:
    path = snapshot_path(settings)
    try:
        snap = load_snapshot(path)
    except SnapshotError as exc:
        reason = f"{exc}. Run backend/scripts/update_sanctions.py."
        return SourceOutcome("unavailable", reason), "sanctions_unavailable: no usable snapshot"
    key = (str(path), path.stat().st_mtime)
    with _SCREENERS_LOCK:
        screener = _SCREENERS.get(key)
        if screener is None:
            _SCREENERS.clear()
            screener = _SCREENERS[key] = Screener(snap)
    problems = snap.problems(now, settings.sanctions_max_age_days)
    if problems:
        text = "; ".join(problems)
        return SourceOutcome("ok", text, screener), f"sanctions_incomplete: {problems[0]}"
    return SourceOutcome("ok", "", screener), None


def _docs(ctx: CaseContext, *types: str) -> list[Any]:
    return [d for d in ctx.docs(*types) if d.status in ("ok", "partial")]


def _cell(doc: Any, name: str) -> str:
    c = doc.fields.get(name)
    return (c.value or "").strip() if c is not None and c.value else ""


def postal_codes(address: str | None) -> list[str]:
    """Six-digit postal codes in a printed address, the one after "Singapore" first."""
    text = address or ""
    after = re.findall(r"singapore\s*,?\s*(\d{6})(?!\d)", text, flags=re.I)
    rest = [p for p in POSTAL_IN_ADDRESS.findall(text) if p not in after]
    return list(dict.fromkeys([*after, *rest]))[:MAX_POSTALS]


def gst_ids(ctx: CaseContext) -> list[str]:
    """GST numbers or UENs of the suppliers on the case's invoices (the GST number when printed)."""
    out = []
    for doc in _docs(ctx, "invoice"):
        ident = _cell(doc, "supplier_gst_no") or _cell(doc, "supplier_uen")
        norm = "".join(ident.split()).upper()
        if norm and norm not in out:
            out.append(norm)
    return out[:MAX_GST_LOOKUPS]


def attach_sources(
    ctx: CaseContext, *, now: datetime | None = None, clients: dict[str, Any] | None = None
) -> list[str]:
    """Fill ``ctx.sources``; returns the partial reasons to add to the case.

    ``clients``: optional ``httpx.Client`` per source name (``gleif``, ``onemap``, ``iras``), for tests."""
    from app.sources.gleif import GleifLookup
    from app.sources.iras import IrasLookup
    from app.sources.onemap import OneMapLookup

    s = ctx.settings
    clients = clients or {}
    reasons: list[str] = []
    cache_dir = Path(s.data_dir) / "verify-cache"
    vault = vault_from_settings(s)
    deadline = Deadline(getattr(s, "sources_budget_s", 45.0))

    # --- sanctions (offline: a local file)
    if not source_status(s, "sanctions")[0]:
        outcome, reason = _sanctions(s, now or datetime.now(UTC))
        ctx.sources["sanctions"] = outcome
        if reason:
            reasons.append(reason)

    # --- GLEIF
    profiles = _docs(ctx, "company_profile", "certificate_of_incorporation")
    if profiles and not source_status(s, "gleif")[0]:
        applicant = ctx.case.applicant
        uen = applicant.uen or next((_cell(d, "uen") for d in profiles if _cell(d, "uen")), "")
        name = applicant.name or next((_cell(d, "company_name") for d in profiles), "")
        try:
            record = GleifLookup(cache_dir, clients.get("gleif"), vault, deadline).lookup(uen, name)
            ctx.sources["gleif"] = SourceOutcome("ok", data=record)
        except SourceUnavailable as exc:
            log.warning("GLEIF unavailable for %s: %s", ctx.case.id, exc)
            ctx.sources["gleif"] = SourceOutcome("unavailable", f"GLEIF could not be asked ({exc})")
            reasons.append("gleif_unavailable: GLEIF could not be asked")

    # --- OneMap
    profile_docs = _docs(ctx, "company_profile")
    if profile_docs and not source_status(s, "onemap")[0]:
        lookup = OneMapLookup(
            cache_dir,
            clients.get("onemap"),
            vault,
            deadline,
            token=s.onemap_token.get_secret_value(),
            email=s.onemap_email,
            password=s.onemap_password.get_secret_value(),
        )
        failed = False
        for postal in dict.fromkeys(
            p for d in profile_docs for p in postal_codes(_cell(d, "registered_address"))
        ):
            try:
                ctx.sources[f"onemap:{postal}"] = SourceOutcome("ok", data=lookup.lookup(postal))
            except SourceUnavailable as exc:
                log.warning("OneMap unavailable for %s: %s", ctx.case.id, exc)
                ctx.sources[f"onemap:{postal}"] = SourceOutcome(
                    "unavailable", f"OneMap could not be asked ({exc})"
                )
                failed = True
        if failed:
            reasons.append("onemap_unavailable: OneMap could not be asked")

    # --- IRAS GST register
    ids = gst_ids(ctx)
    if ids and not source_status(s, "iras")[0]:
        iras = IrasLookup(
            s.iras_api_key.get_secret_value(),
            s.iras_api_secret.get_secret_value(),
            s.iras_base_url,
            cache_dir,
            clients.get("iras"),
            vault,
            deadline,
        )
        failed = False
        for reg_id in ids:
            try:
                ctx.sources[f"iras:{reg_id}"] = SourceOutcome("ok", data=iras.lookup(reg_id))
            except SourceUnavailable as exc:
                log.warning("IRAS unavailable for %s: %s", ctx.case.id, exc)
                ctx.sources[f"iras:{reg_id}"] = SourceOutcome(
                    "unavailable", f"IRAS could not be asked ({exc})"
                )
                failed = True
        if failed:
            reasons.append("iras_unavailable: the GST register could not be asked")
    return reasons
