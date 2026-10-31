"""The second-reading rule for scan flags (app/verify_reading.py)."""

from __future__ import annotations

from typing import Any

import pytest

from app import verify_reading as vr
from app.core.config import Settings
from app.core.models import Flag
from app.core.testing import make_ctx, make_doc


def flag(
    fid: str, rule: str, docs: list[str], severity: str = "high", stage: str = "rules"
) -> Flag:
    return Flag(
        id=fid,
        rule=rule,
        stage=stage,  # type: ignore[arg-type]
        severity=severity,  # type: ignore[arg-type]
        document_ids=docs,
        evidence="e",
        explanation="Something is off.",
    )


def settings(**kw: Any) -> Settings:
    return Settings(_env_file=None, llm_backend="fake", db_path=":memory:", **kw)


def ctx_with(flags: list[Flag], **kw: Any):  # noqa: ANN202
    docs = [
        make_doc("bank_statement", id="d1", file_id="f1"),
        make_doc("invoice", id="d2", file_id="f2"),
    ]
    return make_ctx(
        docs, flags=flags, settings=kw.pop("settings", settings(verify_scan_flags=True)), **kw
    )


@pytest.fixture(autouse=True)
def scans_everywhere(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vr, "is_scan_document", lambda case, document_id, ctx: True)
    monkeypatch.setattr(vr, "other_reader", lambda ctx: {"ocr_backend": "none"})


def shadow_with(ctx, flags: list[Flag]):  # noqa: ANN001, ANN202
    shadow = ctx.case.model_copy(deep=True)
    shadow.flags = flags
    shadow.check_runs = []
    return shadow, []


def test_a_flag_both_readings_raise_stays(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = ctx_with([flag("a", "bs_totals", ["d1"])])
    monkeypatch.setattr(
        vr, "shadow_reading", lambda c, r: shadow_with(c, [flag("z", "bs_totals", ["d1"])])
    )
    out = vr.confirm_scan_flags(ctx)
    assert out["unconfirmed_flags"] == 0 and out["verified_flags"] == 1
    assert ctx.case.flags[0].severity == "high"


def test_a_flag_only_the_first_reading_raised_is_set_aside_not_deleted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = ctx_with([flag("a", "x_directors", ["d1"]), flag("b", "bs_totals", ["d2"])])
    monkeypatch.setattr(
        vr, "shadow_reading", lambda c, r: shadow_with(c, [flag("z", "bs_totals", ["d2"])])
    )
    out = vr.confirm_scan_flags(ctx)
    assert out["unconfirmed_flags"] == 1
    by_id = {f.id: f for f in ctx.case.flags}
    assert by_id["a"].severity == "info" and by_id["a"].explanation.startswith(vr.NOTE)
    assert by_id["b"].severity == "high"
    assert by_id["a"].code_severity is None  # no hard stop can hang on a set-aside flag


def test_the_key_ignores_the_other_documents_a_flag_cites(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = ctx_with([flag("a", "x_remitter_vs_buyer", ["d2", "d1"], stage="crossdoc")])
    monkeypatch.setattr(
        vr,
        "shadow_reading",
        lambda c, r: shadow_with(c, [flag("z", "x_remitter_vs_buyer", ["d2"], stage="crossdoc")]),
    )
    assert vr.confirm_scan_flags(ctx)["unconfirmed_flags"] == 0


def test_duplicates_semantic_and_low_flags_are_not_second_guessed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = ctx_with(
        [
            flag("a", "inv_duplicate", ["d2"]),
            flag("b", "sem_goods_fit", ["d2"], severity="medium", stage="crossdoc"),
            flag("c", "bs_dates", ["d1"], severity="low"),
        ]
    )
    called = []
    monkeypatch.setattr(vr, "shadow_reading", lambda c, r: called.append(1) or shadow_with(c, []))
    assert vr.confirm_scan_flags(ctx) == {} and not called


def test_a_failed_second_reading_drops_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = ctx_with([flag("a", "bs_totals", ["d1"])])

    def boom(c, r):  # noqa: ANN001, ANN202
        raise RuntimeError("network")

    monkeypatch.setattr(vr, "shadow_reading", boom)
    out = vr.confirm_scan_flags(ctx)
    assert out["verification"] == "unavailable" and "RuntimeError" in str(out["warning"])
    assert ctx.case.flags[0].severity == "high"


def test_off_by_default_without_a_model() -> None:
    assert Settings(_env_file=None, llm_backend="fake").verify_scan_flags is False
    ctx = ctx_with([flag("a", "bs_totals", ["d1"])], settings=settings())
    assert vr.confirm_scan_flags(ctx) == {}
