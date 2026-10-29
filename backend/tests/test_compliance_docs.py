"""Lint for the compliance documents (docs/compliance.md section 6).

Headings, caveat, marks, artefact paths, third-party register, required fields, validation
skeleton and the generated open-items list. No network, no wall clock.
"""

from __future__ import annotations

import glob
import re
from pathlib import Path

import pytest

from app.core.config import Settings

REPO = Path(__file__).resolve().parents[2]
DOCS = REPO / "docs"

AI = DOCS / "ai-inventory.md"
REG = DOCS / "regulatory-mapping.md"
VAL = DOCS / "validation-report.md"
CAT = DOCS / "check-catalogue.md"
THREE = (AI, REG, VAL)
FOUR = (*THREE, CAT)

HEADINGS = {
    "ai-inventory.md": [
        "1 Entry",
        "2 AI and model components",
        "3 Materiality assessment",
        "4 MAS Guidelines, by area",
        "5 Model risk checklist",
        "6 Risks and controls",
        "7 Open questions for the sponsor",
        "8 Change history",
    ],
    "regulatory-mapping.md": [
        "1 Instruments",
        "2 Obligation to feature to evidence",
        "3 Escalation flow",
        "4 What we do not claim",
        "5 Sources",
        "6 Open [confirm] items",
    ],
    "validation-report.md": [
        "1 Scope and version",
        "2 Method and honest framing",
        "3 Fraud detection",
        "4 False alarms",
        "5 Extraction",
        "6 Classification",
        "7 Injection canaries",
        "8 Cost and speed",
        "9 Prioritisation",
        "10 Fairness slice",
        "11 Deck acceptance",
        "12 Analyst burden",
        "13 Known misses and limits",
        "14 Change log",
        "15 Sign-off",
    ],
}
EVAL_MARKERS = [
    "scope",
    "recall",
    "false_alarms",
    "extraction",
    "classification",
    "canaries",
    "cost",
    "ranking",
    "fairness",
    "acceptance",
    "burden",
    "limits",
    "changelog",
]
ENTRY_FIELDS = [
    "System name, version",
    "Owner",
    "Purpose",
    "Process it supports",
    "Users",
    "Decision role",
    "Inputs",
    "Outputs",
    "Components",
    "Out of scope",
    "Materiality",
    "Validation status",
    "Known limits",
    "Review date, next review",
]
MARK = re.compile(r"`?\[(?:found|found, secondary|confirm)\]`?")
BANNED = re.compile(r"\bcompl(?:iant|ies)\b", re.I)
VERBS = ("GET", "POST", "DELETE", "PUT", "PATCH")
PATH_SUFFIXES = (".py", ".md", ".yml", ".json", ".tsx", ".ts")


# --- helpers ----------------------------------------------------------------------------------


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def sections(text: str) -> dict[str, str]:
    """``## heading`` -> body (fenced code is not parsed specially: no doc uses ``## `` in it)."""
    parts = re.split(r"^## (.+)$", text, flags=re.M)
    return {parts[i].strip(): parts[i + 1] for i in range(1, len(parts) - 1, 2)}


def h2(text: str) -> list[str]:
    return [h.strip() for h in re.findall(r"^## (.+)$", text, flags=re.M)]


def cells(row: str) -> list[str]:
    return [c.strip() for c in row.strip().strip("|").split("|")]


def table_rows(body: str) -> list[list[str]]:
    """Body rows of every pipe table in ``body`` (header and separator dropped)."""
    rows: list[list[str]] = []
    lines = body.splitlines()
    for i, line in enumerate(lines):
        if not line.startswith("|"):
            continue
        if i + 1 < len(lines) and re.match(r"^\|[-| ]+\|$", lines[i + 1]):
            continue  # header row
        if re.match(r"^\|[-| ]+\|$", line):
            continue
        rows.append(cells(line))
    return rows


def content_lines(text: str) -> list[str]:
    """Lines without leading HTML comments (the generated-file marker)."""
    lines = text.splitlines()
    while lines and lines[0].startswith("<!--"):
        lines.pop(0)
    return lines


def lint_marks(reg_text: str) -> list[str]:
    """Defects: a section-1 row without a mark, or a section-2 row without a mark or a
    "plan principle" source."""
    secs = sections(reg_text)
    defects: list[str] = []
    for row in table_rows(secs["1 Instruments"]):
        if not MARK.search(" ".join(row)):
            defects.append(f"instrument row without a mark: {row[0][:60]}")
    for row in table_rows(secs["2 Obligation to feature to evidence"]):
        if len(row) >= 2 and not (MARK.search(row[1]) or "plan principle" in row[1]):
            defects.append(f"obligation row without a mark: {row[0]}")
    return defects


def path_tokens(text: str) -> list[str]:
    out: list[str] = []
    for tok in re.findall(r"`([^`]+)`", text):
        if re.search(r"\s", tok) or tok.startswith(VERBS):
            continue
        if "/" in tok or tok.endswith(PATH_SUFFIXES):
            out.append(tok)
    return out


def resolve(token: str) -> str | None:
    """None if the token resolves in the repo (path glob and, for ``path::symbol``, the symbol
    text in the file); otherwise the reason."""
    path, _, symbol = token.partition("::")
    path = path.rstrip("/") or path
    matches = glob.glob(str(REPO / path), recursive=True)
    if not matches:
        return f"path does not exist: {path}"
    if symbol:
        target = Path(matches[0])
        leaf = symbol.split(".")[-1]
        if not target.is_file() or leaf not in target.read_text(encoding="utf-8"):
            return f"symbol {symbol} not found in {path}"
    return None


def unresolved(reg_text: str) -> list[str]:
    """Section 2, Code artefact and Automated evidence columns (cell level, `NOT BUILT` accepted)."""
    out: list[str] = []
    for row in table_rows(sections(reg_text)["2 Obligation to feature to evidence"]):
        for cell in (row[3], row[4]):
            if "NOT BUILT" in cell:
                continue
            for tok in path_tokens(cell):
                why = resolve(tok)
                if why:
                    out.append(f"{row[0]}: {why}")
    return out


EVAL_RESULTS_COMMITTED = {"docs/eval-results/baseline.json", "docs/eval-results/timed-test.csv"}


def unresolved_elsewhere(text: str) -> list[str]:
    """Every other line of a document: backticked paths resolve unless the line says NOT BUILT."""
    out: list[str] = []
    for line in text.splitlines():
        if "NOT BUILT" in line:
            continue
        for tok in path_tokens(line):
            # eval outputs (docs/eval-results/*.csv) are gitignored and exist only after someone has run the
            # eval; only the committed baseline and timed-test sheet are always there
            if tok.startswith("docs/eval-results/") and tok not in EVAL_RESULTS_COMMITTED:
                continue
            why = resolve(tok)
            if why:
                out.append(why)
    return out


def not_built_paths(text: str) -> list[str]:
    return re.findall(r"NOT BUILT: ([\w./*-]+)", text)


def confirm_keys(reg_text: str) -> list[str]:
    secs = sections(reg_text)
    keys: list[str] = []
    for n, name in ((1, "1 Instruments"), (2, "2 Obligation to feature to evidence")):
        for row in table_rows(secs[name]):
            if "[confirm]" in " ".join(row[:2]) if n == 2 else "[confirm]" in " ".join(row):
                key = row[0]
                keys.append(f"{key} (section {n})")
    return keys


def listed_open_items(reg_text: str) -> list[str]:
    body = sections(reg_text)["6 Open [confirm] items"]
    return [m.strip() for m in re.findall(r"^- (.+)$", body, flags=re.M)]


# --- headings, caveat, wording -----------------------------------------------------------------


@pytest.mark.parametrize("path", THREE, ids=lambda p: p.name)
def test_headings_exact_and_in_order(path: Path) -> None:
    assert h2(read(path)) == HEADINGS[path.name]


@pytest.mark.parametrize("path", FOUR, ids=lambda p: p.name)
def test_caveat_on_line_two(path: Path) -> None:
    lines = content_lines(read(path))
    assert lines[0].startswith("# ")
    assert "not legal advice" in lines[1]
    assert "All data is fictitious" in lines[1]


@pytest.mark.parametrize("path", FOUR, ids=lambda p: p.name)
def test_nothing_is_called_compliant(path: Path) -> None:
    assert BANNED.findall(read(path)) == []


@pytest.mark.parametrize("path", THREE, ids=lambda p: p.name)
def test_documents_stay_short(path: Path) -> None:
    assert len(read(path).splitlines()) <= 300  # spec: about 250 lines


# --- marks -------------------------------------------------------------------------------------


def test_marks_lint_passes_on_the_committed_mapping() -> None:
    assert lint_marks(read(REG)) == []


def test_every_instrument_row_has_exactly_the_documented_shape() -> None:
    rows = table_rows(sections(read(REG))["1 Instruments"])
    assert len(rows) == 7
    assert all(len(r) == 5 for r in rows)


def test_marks_lint_fails_for_a_stub_row_without_a_mark() -> None:
    text = read(REG)
    stub = "| Stub instrument | no mark here | none | says what it requires | nothing |\n"
    broken = text.replace("\n## 2 Obligation", "\n" + stub + "\n## 2 Obligation", 1)
    assert lint_marks(broken) == ["instrument row without a mark: Stub instrument"]
    # and a stub obligation row
    row = "| O99 | Stub obligation | f | `README.md` | `README.md` | d | g |\n"
    broken2 = text.replace("\n## 3 Escalation", "\n" + row + "\n## 3 Escalation", 1)
    assert "obligation row without a mark: O99" in lint_marks(broken2)


def test_banned_word_regex_catches_variants() -> None:
    assert BANNED.search("the tool is Compliant") and BANNED.search("it complies")
    assert not BANNED.search("compliance analysts and the compliance team")


# --- artefacts ---------------------------------------------------------------------------------


def test_every_obligation_row_resolves_its_artefacts_or_says_not_built() -> None:
    assert unresolved(read(REG)) == []


def test_other_backticked_paths_resolve() -> None:
    for path in THREE:
        assert unresolved_elsewhere(read(path)) == [], path.name


def test_artefact_resolver_flags_a_missing_path_and_a_missing_symbol() -> None:
    assert resolve("backend/app/does_not_exist.py::X")
    assert resolve("backend/app/score.py::NoSuchSymbolAnywhere")
    assert resolve("backend/app/score.py::run_stage") is None
    assert resolve("backend/tests/test_api_*.py") is None
    text = read(REG).replace("`backend/app/review.py`", "`backend/app/nope.py`", 1)
    assert any("nope.py" in d for d in unresolved(text))


def test_not_built_is_accepted_but_counted_and_open_items_exist(capsys) -> None:  # noqa: ANN001
    text = read(REG)
    not_built = not_built_paths(text) + not_built_paths(read(AI))
    stale = sorted({p for p in not_built if "*" not in p and (REPO / p).exists()})
    with capsys.disabled():
        print(f"\ncompliance docs: {len(not_built)} NOT BUILT reference(s)")
        if stale:
            print(f"compliance docs: NOT BUILT but now present (update the docs): {stale}")
    assert listed_open_items(text), (
        "the open [confirm] list must not be empty while rows are [confirm]"
    )
    assert any("[confirm]" in r for r in text.splitlines())


# --- open [confirm] items ----------------------------------------------------------------------


def test_open_items_list_equals_the_confirm_rows() -> None:
    text = read(REG)
    assert sorted(listed_open_items(text)) == sorted(confirm_keys(text))


def test_open_items_list_detects_a_missing_entry() -> None:
    text = read(REG).replace("- O14 (section 2)\n", "")
    assert sorted(listed_open_items(text)) != sorted(confirm_keys(text))


# --- inventory ---------------------------------------------------------------------------------


def test_every_llm_setting_appears_in_the_third_party_register() -> None:
    register = sections(read(AI))["2 AI and model components"]
    names = [n for n in Settings.model_fields if n.startswith(("llm_", "anthropic_"))]
    names.append("max_usd_per_day")
    assert {"llm_backend", "llm_base_url", "llm_model_cheap", "llm_model_strong"} <= set(names)
    assert {"anthropic_api_key", "anthropic_model", "anthropic_workspace_id"} <= set(names)
    missing = [f"GLDB_{n.upper()}" for n in names if f"GLDB_{n.upper()}" not in register]
    assert missing == []


def test_entry_table_has_every_required_field() -> None:
    rows = table_rows(sections(read(AI))["1 Entry"])
    assert [r[0] for r in rows] == ENTRY_FIELDS
    assert all(r[1] for r in rows)


def test_register_names_the_six_components_and_the_pseudonymisation_gap() -> None:
    body = sections(read(AI))["2 AI and model components"]
    for name in (
        "Extraction (cascade)",
        "Classification C1-C5",
        "Rules and cross-document checks",
        "Forensics",
        "Name matching",
        "Intermediary",
    ):
        assert f"| {name} |" in body
    assert "not** implemented" in body  # pseudonymisation of outbound text


def test_inventory_has_four_areas_and_five_information_paper_rows() -> None:
    area = sections(read(AI))["4 MAS Guidelines, by area"]
    assert re.findall(r"^### (Area \d)", area, flags=re.M) == [f"Area {n}" for n in range(1, 5)]
    assert len(table_rows(sections(read(AI))["5 Model risk checklist"])) == 5


def test_inventory_lists_the_three_sponsor_questions() -> None:
    body = sections(read(AI))["7 Open questions for the sponsor"]
    assert re.findall(r"^- (Q\d+):", body, flags=re.M) == ["Q9", "Q10", "Q11"]


# --- validation skeleton -----------------------------------------------------------------------


def test_validation_skeleton_markers_once_each_in_order() -> None:
    text = read(VAL)
    begins = re.findall(r"<!-- BEGIN eval:(\w+) -->", text)
    ends = re.findall(r"<!-- END eval:(\w+) -->", text)
    assert begins == EVAL_MARKERS
    assert ends == EVAL_MARKERS
    for name in EVAL_MARKERS:
        block = re.search(
            rf"<!-- BEGIN eval:{name} -->\n(.*?)\n<!-- END eval:{name} -->", text, flags=re.S
        )
        # compliance.md 3.3 and criterion 4: before the eval the block is the skeleton text,
        # after `eval.py --write-validation-report` it is filled (or says "not run" with a reason)
        assert block and block.group(1).strip()
    assert len(EVAL_MARKERS) == 13


def test_validation_status_line_and_empty_signoff() -> None:
    text = read(VAL)
    status = re.findall(r"^Status: .*$", text, flags=re.M)
    assert len(status) == 1
    # skeleton: exactly "Status: NOT VALIDATED"; after the eval it names the ruleset version
    assert status[0] == "Status: NOT VALIDATED" or re.match(
        r"Status: (NOT VALIDATED|VALIDATED FOR)\b.*\brs-[0-9a-f]+", status[0]
    )
    rows = table_rows(sections(text)["15 Sign-off"])
    assert rows and all(all(c == "" for c in r[1:]) for r in rows)


def test_validation_report_has_no_hand_written_metric() -> None:
    text = read(VAL)
    outside = re.sub(r"<!-- BEGIN eval:\w+ -->.*?<!-- END eval:\w+ -->", "", text, flags=re.S)
    # no percentages and no decimals outside the markers; the only digits are section numbers,
    # the "9.4" plan reference, the count "13"/"15" style words and the heading numbers
    assert "%" not in outside
    assert not re.search(r"\d+\.\d+\b(?<!9\.4)", outside.replace("plan 9.4", ""))
