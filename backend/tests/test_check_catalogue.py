"""Tests for scripts/gen_check_catalogue.py (docs/compliance.md section 6)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.checks.registry import CheckMeta, all_checks
from app.core.provenance import build_provenance
from app.core.testing import make_ctx
from scripts import gen_check_catalogue as gen

REPO_ROOT = Path(__file__).resolve().parents[2]
CATALOGUE = REPO_ROOT / "docs" / "check-catalogue.md"


def _stub(rule: str, **kw) -> CheckMeta:  # noqa: ANN003
    base = {
        "rule": rule,
        "stage": "rules",
        "severity": "low",
        "applies_to": frozenset({"invoice"}),
        "description": "A stub check.",
        "func": lambda ctx: [],
    }
    base.update(kw)
    return CheckMeta(**base)


def _section(text: str, heading: str) -> str:
    """Body of the ``## heading`` section."""
    m = re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    assert m, f"heading {heading!r} not found"
    return m.group(1)


def _rule_ids(section: str) -> list[str]:
    return re.findall(r"^\| `([^`]+)` \|", section, re.M)


# --- the plan list itself -------------------------------------------------------------------


def test_plan_rules_has_52_rules_with_the_plan_section_counts() -> None:
    counts: dict[str, int] = {}
    for section in gen.PLAN_RULES.values():
        counts[section] = counts.get(section, 0) + 1
    assert counts == {"4.1": 9, "4.2": 8, "4.3": 5, "4.4": 5, "4.5": 14, "4.6": 11}
    assert len(gen.PLAN_RULES) == 52


# --- determinism ------------------------------------------------------------------------------


def test_render_is_deterministic_lf_and_ends_with_newline() -> None:
    checks = all_checks()
    first = gen.render_catalogue(checks)
    assert first == gen.render_catalogue(list(reversed(checks)))  # input order is irrelevant
    assert first.endswith("\n") and not first.endswith("\n\n")
    assert "\r" not in first
    # no timestamp-looking sequence (ISO date or clock time)
    assert not re.search(r"\d{4}-\d{2}-\d{2}|\d{2}:\d{2}:\d{2}", first)


# --- completeness -----------------------------------------------------------------------------


def test_every_registered_rule_listed_once_under_its_stage() -> None:
    checks = all_checks()
    text = gen.render_catalogue(checks)
    headings = {
        "forensics": "Forensics",
        "rules": "Rules (per document)",
        "crossdoc": "Cross-document",
    }
    seen: list[str] = []
    for stage, heading in headings.items():
        ids = _rule_ids(_section(text, heading))
        assert ids == sorted(ids)
        expected = sorted(c.rule for c in checks if c.stage == stage)
        assert ids == expected
        seen += ids
    assert len(seen) == len(set(seen)) == len(checks)
    # stage sections come in the fixed order
    order = [text.index(f"## {h}") for h in headings.values()]
    assert order == sorted(order)


def test_row_carries_severity_applies_to_use_cases_and_version() -> None:
    meta = _stub(
        "zz_row",
        severity="high",
        applies_to=frozenset({"invoice", "bank_statement"}),
        requires=frozenset({"contract"}),
        use_cases=frozenset({"loan", "aml"}),
        tamper="edited total",
        version="3",
    )
    text = gen.render_catalogue([meta])
    row = next(line for line in text.splitlines() if line.startswith("| `zz_row`"))
    assert row == (
        "| `zz_row` | high | bank_statement, invoice | contract | aml, loan | A stub check. "
        "| edited total | 3 |"
    )


def test_wildcard_applies_to_prints_any_file_and_missing_tamper_prints_dash() -> None:
    meta = _stub("zz_any", stage="forensics", applies_to=frozenset({"*"}), severity="info")
    row = next(
        line for line in gen.render_catalogue([meta]).splitlines() if line.startswith("| `zz_any`")
    )
    cells = [c.strip() for c in row.strip("|").split("|")]
    assert cells[1] == "info"  # an info check shows its default severity
    assert cells[2] == "any file"
    assert cells[3] == "-"  # requires
    assert cells[6] == "-"  # tamper


# --- escaping ---------------------------------------------------------------------------------


def test_pipe_and_newline_in_description_are_escaped() -> None:
    meta = _stub("zz_esc", description="a|b\nsecond  line", tamper="x|y")
    text = gen.render_catalogue([meta])
    row = next(line for line in text.splitlines() if line.startswith("| `zz_esc`"))
    assert "a\\|b second line" in row
    assert "x\\|y" in row
    # the escaped pipes do not add columns
    assert len(re.split(r"(?<!\\)\|", row.strip())) - 2 == 8


# --- gap lists --------------------------------------------------------------------------------


def test_planned_but_not_registered_lists_exactly_the_missing_rules() -> None:
    checks = [c for c in all_checks() if c.rule not in {"noise_mismatch", "bs_benford"}]
    text = gen.render_catalogue(checks)
    section = _section(text, "Planned but not registered (gap, plan 0.6)")
    assert _rule_ids(section) == ["bs_benford", "noise_mismatch"]
    assert "| `bs_benford` | 4.1 |" in section
    assert "| `noise_mismatch` | 4.5 |" in section
    assert "Planned in the plan but not registered: 2" in text


def test_planned_gap_says_none_when_nothing_is_missing() -> None:
    plan = {"zz_a": "4.1"}
    text = gen.render_catalogue([_stub("zz_a")], plan_rules=plan)
    assert _section(text, "Planned but not registered (gap, plan 0.6)").strip() == "None"
    assert "but not registered: 0" in text


def test_registered_but_unplanned_shows_reason() -> None:
    text = gen.render_catalogue(
        [_stub("zz_extra"), _stub("file_reuse", stage="crossdoc"), _stub("doc_unreadable")],
        plan_rules={},
    )
    section = _section(text, "Registered but not in the plan")
    rows = {
        r: line for r in _rule_ids(section) for line in section.splitlines() if f"`{r}`" in line
    }
    assert set(rows) == {"zz_extra", "file_reuse", "doc_unreadable"}
    assert rows["zz_extra"].endswith("| unplanned |")
    assert "duplicate detection" in rows["file_reuse"]
    assert "plan 8.5" in rows["doc_unreadable"]


def test_unplanned_says_none_when_every_rule_is_in_the_plan() -> None:
    text = gen.render_catalogue([_stub("zz_a")], plan_rules={"zz_a": "4.1"})
    assert _section(text, "Registered but not in the plan").strip() == "None"


def test_real_registry_has_no_unexplained_unplanned_rule() -> None:
    allowed = set(gen.BUILTIN_RULES) | set(gen.ADDED_RULES)
    unplanned = {c.rule for c in all_checks()} - set(gen.PLAN_RULES)
    assert unplanned <= allowed, f"unplanned rules without a reason: {sorted(unplanned - allowed)}"


# --- version ----------------------------------------------------------------------------------


def test_header_ruleset_version_equals_provenance() -> None:
    ctx = make_ctx()
    expected = build_provenance(ctx).ruleset_version
    text = gen.render_catalogue(all_checks())
    assert f"Ruleset version: `{expected}`" in text
    assert gen.ruleset_version(all_checks()) == expected


def test_header_has_caveat_on_the_line_after_the_title() -> None:
    lines = gen.render_catalogue([_stub("zz_a")]).splitlines()
    assert lines[0].startswith("<!-- GENERATED")
    assert lines[1] == "# Check catalogue"
    assert "not legal advice" in lines[2]


# --- committed file ---------------------------------------------------------------------------


def test_committed_catalogue_is_fresh() -> None:
    assert CATALOGUE.read_bytes().decode("utf-8") == gen.render_catalogue(gen.collect()), (
        "docs/check-catalogue.md is stale: run "
        "`uv run python backend/scripts/gen_check_catalogue.py`"
    )


# --- CLI --------------------------------------------------------------------------------------


def test_check_mode_exit_codes_and_writes_nothing(tmp_path: Path) -> None:
    target = tmp_path / "cat.md"
    # missing file: stale, and --check must not create it
    assert gen.main(["--out", str(target), "--check"]) == 1
    assert not target.exists()

    assert gen.main(["--out", str(target)]) == 0
    assert target.read_text(encoding="utf-8") == gen.render_catalogue(gen.collect())
    assert gen.main(["--out", str(target), "--check"]) == 0

    stale = target.read_text(encoding="utf-8") + "extra\n"
    target.write_text(stale, encoding="utf-8")
    assert gen.main(["--out", str(target), "--check"]) == 1
    assert target.read_text(encoding="utf-8") == stale  # not rewritten


def test_discover_failure_exits_1_and_writes_no_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom() -> list[CheckMeta]:
        raise ImportError("check module app.checks.broken failed to import: nope")

    monkeypatch.setattr(gen, "collect", boom)
    target = tmp_path / "cat.md"
    assert gen.main(["--out", str(target)]) == 1
    assert not target.exists()
    assert "app.checks.broken" in capsys.readouterr().err


# --- API parity -------------------------------------------------------------------------------


def test_api_rules_equal_catalogue_rows(settings, db) -> None:  # noqa: ANN001
    from app.main import create_app

    with TestClient(create_app(settings, db=db)) as client:
        body = client.get("/rules").json()
    api = {r["rule"]: r for r in body["rules"]}
    text = gen.render_catalogue(all_checks())
    rows = {}
    for stage_heading in ("Forensics", "Rules (per document)", "Cross-document"):
        for line in _section(text, stage_heading).splitlines():
            m = re.match(r"^\| `([^`]+)` \| (\w+) \|", line)
            if m:
                rows[m.group(1)] = m.group(2)
    assert set(rows) == set(api)
    assert rows == {rule: r["severity"] for rule, r in api.items()}
    stages = {r: v["stage"] for r, v in api.items()}
    assert stages == {c.rule: c.stage for c in all_checks()}
