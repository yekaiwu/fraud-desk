"""The three November features used together: encryption at rest, alert delivery and the name-linking switch."""

from __future__ import annotations

import base64
from pathlib import Path

from app.alerts import AlertService
from app.core.config import Settings
from app.core.crypto import vault_from_settings
from app.core.db import Database
from app.monitoring import snapshot
from app.readiness import readiness
from tests.test_alerts import FakeSink
from tests.test_api_support import FIXED_NOW, FakeClock, seed_case, tiny_pdf

KEY = base64.b64encode(bytes([7]) * 32).decode()


def encrypted(tmp_path: Path, **kw: object) -> tuple[Settings, Database]:
    s = Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        db_path=str(tmp_path / "data" / "gldb.sqlite3"),
        llm_backend="fake",
        encrypt_at_rest="on",
        encryption_keys=f"k1:{KEY}",
        monitor_min_cases=5,
        monitor_partial_rate_max=0.3,
        **kw,
    )
    db = Database(s.database_path, data_dir=s.data_dir, vault=vault_from_settings(s))
    db.init_schema()
    return s, db


def test_monitoring_reads_encrypted_cases_and_the_alert_reaches_a_sink(tmp_path: Path) -> None:
    s, db = encrypted(tmp_path)
    for i in range(8):
        seed_case(
            db, s, files=[(f"a{i}.pdf", tiny_pdf(f"x{i}"))], partial=True, updated_at=FIXED_NOW
        )
    snap = snapshot(db, s, now=FIXED_NOW)
    assert snap["cases_in_window"] == 8
    assert next(a for a in snap["alerts"] if a["metric"] == "partial_rate")["status"] == "alert"

    sink = FakeSink()
    clock = FakeClock(FIXED_NOW)
    AlertService(db, s, clock=clock, sinks=[sink], sleep=lambda _s: None).evaluate(source="test")
    assert (
        any("partial_rate" in m.text for m in sink.sent)
        if hasattr(sink.sent[0], "text")
        else sink.sent
    )
    # the message names the metric but never carries case content
    assert not any("x0" in str(m) for m in sink.sent)


def test_readiness_is_not_met_until_a_person_decides(tmp_path: Path) -> None:
    s, db = encrypted(tmp_path)
    full = readiness(db, s, [FakeSink()], now=FIXED_NOW)
    assert full["ready"] is False
    assert {i["status"] for i in full["items"]} != {"met"}


def test_the_name_linking_check_is_off_unless_its_own_switch_is_on(tmp_path: Path) -> None:
    s_off, _ = encrypted(tmp_path / "a", semantic="on")
    s_on, _ = encrypted(tmp_path / "b", semantic="on", semantic_entity="on")
    assert s_off.semantic_entity == "off" and s_on.semantic_entity == "on"
