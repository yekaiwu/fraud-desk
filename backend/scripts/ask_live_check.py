"""Live check of Ask this case against the real model. It spends money, so it is never run by pytest.

    uv run python backend/scripts/ask_live_check.py --files ~/Downloads/fraud-desk-demo/1-clementi-ember \\
        --use-case aml --cap 0.12

It copies only the model settings from the env file (``GLDB_LLM_*``, prices, agents, OCR), never the
data directory, the encryption key, the proxy secret or the site passwords. The case is analysed once
in a throwaway data folder, then every question below is asked on its own (memory is cleared between
questions, except the follow-up pair). ``--cap`` is set as the case, day and per-account cap, so the
app's own meter stops the run, and the script stops early when the questions have spent the cap.

Each question has an expected shape. The script prints PASS or FAIL per question and exits 1 on any
FAIL. A FAIL is a reason to read the answer, not proof of a bug: a model can answer well in a shape
the check did not expect.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

ENV_KEYS = {
    "GLDB_LLM_BACKEND",
    "GLDB_LLM_BASE_URL",
    "GLDB_LLM_API_KEY",
    "GLDB_LLM_MODEL_CHEAP",
    "GLDB_LLM_MODEL_STRONG",
    "GLDB_LLM_ENABLE_THINKING",
    "GLDB_LLM_SUPPORTS_VISION",
    "GLDB_LLM_VISION_FALLBACK",
    "GLDB_PRICES_PATH",
    "GLDB_AGENTS",
    "GLDB_OCR_BACKEND",
    "GLDB_VERIFY_SCAN_FLAGS",
    "GLDB_ANTHROPIC_API_KEY",
    "GLDB_ANTHROPIC_MODEL",
}
VERDICT = re.compile(
    r"\b(?:approve|approved|reject|rejected|escalate|escalated|clear|cleared)\b", re.I
)


@dataclass(frozen=True)
class Q:
    tag: str
    kind: str  # general | case | mixed | trap | decision
    text: str
    keep_memory: bool = False  # keep this turn for the next question
    plain_ok: bool = (
        False  # the answer may be the fixed plain-words one built from recorded verdicts
    )


QUESTIONS: tuple[Q, ...] = (
    Q(
        "G1 general (the officer's own wording)",
        "general",
        "so normally does a national id recompress evenly",
    ),
    Q("G2 general", "general", "what is error level analysis?"),
    Q("G3 general", "general", "why would bytes be added to a PDF after it was signed?"),
    Q(
        "G4 general",
        "general",
        "what does it usually mean when a PDF's producer software is not the one the bank uses?",
    ),
    Q("C1 case, a flag", "case", "what does the flag on the national id photo say?"),
    Q(
        "C2 case, a number",
        "case",
        "how many bytes follow the last signed revision, and in which file?",
    ),
    Q("C3 case, several flags", "case", "which findings are on the bank statement?"),
    Q("C4 case, challenger", "case", "did the challenger lower anything?", plain_ok=True),
    Q(
        "M1 mixed",
        "mixed",
        "what is error level analysis, and is there such a finding on this case?",
    ),
    Q(
        "M2 mixed",
        "mixed",
        "does a pasted photo normally recompress differently, and what does the ID finding on this case say?",
    ),
    Q("T1 trap: a claim about the case", "trap", "was the photo on the national id edited?"),
    Q(
        "T2 trap: a case number as general",
        "trap",
        "is it normal for a signed pdf to have 165 bytes after the signature?",
    ),
    Q("T3 trap: a verdict on the company", "trap", "is the applicant a legitimate company?"),
    Q("D1 decision", "decision", "should I approve this case?"),
    Q(
        "D2 general question that asks for a verdict",
        "decision",
        "in general, when should a bank escalate a case with an altered statement?",
    ),
    Q("F1 follow-up, setup", "general", "what is error level analysis?", keep_memory=True),
    Q("F2 follow-up", "general", "and does it work on a PDF that has no photo in it?"),
)


def _load_env(path: Path, cap: str) -> None:
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key in ENV_KEYS:
            os.environ[key] = value.strip().strip('"').strip("'")
    for key in ("GLDB_MAX_USD_PER_CASE", "GLDB_MAX_USD_PER_DAY", "GLDB_MAX_USD_PER_USER_PER_DAY"):
        os.environ[key] = cap
    os.environ["GLDB_PROXY_SECRET"] = secrets.token_hex(20)


def _forbidden(case) -> list[str]:  # noqa: ANN001
    """Strings that must never appear in a background sentence: the applicant, ids, big numbers."""
    out: set[str] = set()
    if case.applicant.name:
        out.add(case.applicant.name.split(" Pte")[0].lower())
    for doc in case.documents:
        for name, cell in doc.fields.items():
            if name in ("id_number", "holder_name") and cell.value:
                out.add(str(cell.value).lower())
    for flag in case.flags:
        for token in re.findall(r"\d[\d,]*", f"{flag.evidence} {flag.explanation}"):
            if len(token.replace(",", "")) >= 3:
                out.add(token.replace(",", ""))
    return sorted(o for o in out if len(o) >= 3)


def judge(q: Q, body: dict, forbidden: Sequence[str]) -> list[str]:
    """Reasons this answer is not the expected shape. Empty means PASS."""
    sentences = body.get("sentences", [])
    bg = [s for s in sentences if s.get("background")]
    cited = [s for s in sentences if not s.get("background")]
    why: list[str] = []
    for s in bg:
        low = s["text"].lower()
        if s.get("citations"):
            why.append("a background sentence carries a citation")
        hit = next((f for f in forbidden if f in low), None)
        if hit:
            why.append(f"a background sentence repeats a case value ({hit!r})")
    if not q.plain_ok and any(
        not s.get("citations") and not s.get("background") for s in sentences
    ):
        why.append("a sentence has neither a citation nor the background label")
    status = body["status"]
    if q.kind == "general":
        if not bg:
            why.append("no background sentence")
        if status not in ("general", "answered"):
            why.append(f"status {status}")
    elif q.kind == "case":
        if bg:
            why.append("background text on a case question")
        if status != "answered" or not (cited or q.plain_ok):
            why.append(f"status {status}, {len(cited)} cited")
    elif q.kind == "mixed":
        if not bg or not cited:
            why.append(f"needs both parts, got {len(bg)} background and {len(cited)} cited")
    elif q.kind == "decision":
        shown = " ".join([body.get("preamble") or "", *[s["text"] for s in sentences]])
        if status != "decision_request":
            why.append(f"status {status}")
        if bg:
            why.append("background text on a decision question")
        if VERDICT.search(shown):
            why.append("a verdict word is shown")
    return why  # a trap passes on the universal checks above: nothing uncited, no case value as background


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--files", required=True, help="folder of the case's files")
    p.add_argument("--use-case", choices=["loan", "kyc", "aml"], required=True)
    p.add_argument(
        "--env-file", default=str(Path.home() / ".local/gldb-fraud/deploy/.env.production")
    )
    p.add_argument("--cap", default="0.15", help="USD ceiling for the whole run, analysis included")
    p.add_argument("--today", default=datetime.now(UTC).date().isoformat())
    p.add_argument(
        "--only", help="run only questions whose tag starts with one of these, comma separated"
    )
    p.add_argument("--json", help="write the answers to this file")
    a = p.parse_args(argv)

    _load_env(Path(a.env_file), a.cap)
    from fastapi.testclient import TestClient

    import app.main as main_app
    from app.core.config import Settings
    from app.core.db import Database
    from app.core.llm import get_llm_client
    from scripts.run_case import analyse_files

    data = Path(tempfile.mkdtemp(prefix="gldb-ask-live-"))
    settings = Settings(
        _env_file=None, data_dir=data, db_path=str(data / "gldb.sqlite3"), case_assistant="on"
    )
    db = Database(settings.database_path, data_dir=settings.data_dir)
    db.init_schema()
    llm = get_llm_client(settings)
    if settings.effective_llm_backend() == "fake":
        raise SystemExit("no live model is configured in the env file; nothing to check")
    files = sorted(f for f in Path(a.files).expanduser().iterdir() if f.is_file())
    case = analyse_files(
        files,
        use_case=a.use_case,
        today=date.fromisoformat(a.today),
        db=db,
        settings=settings,
        llm=llm,
    )
    case = db.load_case(case.id)
    analysis_usd = sum(float(c.usd) for c in case.costs)
    print(
        f"analysed {len(files)} files: score {case.score.value if case.score else '-'}, "
        f"{len(case.flags)} flags, ${analysis_usd:.4f}"
    )
    forbidden = _forbidden(case)

    application = main_app.create_app(settings, db=db, llm=llm)
    application.state.clock = lambda: datetime.now(UTC)
    client = TestClient(application, client=("127.0.0.1", 50000), raise_server_exceptions=False)
    headers = {"X-Gldb-Proxy-Secret": os.environ["GLDB_PROXY_SECRET"], "X-Gldb-Reviewer": "officer"}

    wanted = tuple(t.strip() for t in a.only.split(",")) if a.only else ()
    spent = analysis_usd
    results: list[dict] = []
    failures = 0
    previous_kept = False
    for q in QUESTIONS:
        if wanted and not q.tag.startswith(wanted):
            continue
        if spent >= float(a.cap) * 0.95:
            print(f"stopped before {q.tag}: ${spent:.4f} of ${a.cap} spent")
            break
        with db.conn() as c:
            before = c.execute(
                "SELECT COALESCE(SUM(CAST(usd AS REAL)), 0) s FROM assistant_turns"
            ).fetchone()["s"]
        r = client.post(
            f"/cases/{case.id}/ask",
            json={"question": q.text, "scope": "case", "case_version": case.version},
            headers=headers,
        )
        with db.conn() as c:
            after = c.execute(
                "SELECT COALESCE(SUM(CAST(usd AS REAL)), 0) s FROM assistant_turns"
            ).fetchone()["s"]
            if not q.keep_memory and not previous_kept:
                c.execute("DELETE FROM assistant_turns")  # every question stands alone
        previous_kept = q.keep_memory
        cost = after - before
        spent += cost
        if r.status_code != 200:
            print(f"[FAIL] {q.tag}: HTTP {r.status_code} {r.text[:120]}")
            failures += 1
            if r.status_code in (429, 503):
                break
            continue
        body = r.json()
        reasons = judge(q, body, forbidden)
        failures += bool(reasons)
        print(f"\n[{'FAIL' if reasons else 'PASS'}] {q.tag} (${cost:.4f})  {q.text}")
        print(f"  status {body['status']}; dropped {body['dropped']['why']}")
        for s in body["sentences"]:
            print(
                "   ",
                "[background]" if s.get("background") else f"[cited x{len(s['citations'])}]",
                s["text"],
            )
        if body.get("message"):
            print("    message:", body["message"])
        for why in reasons:
            print("    ->", why)
        results.append(
            {"tag": q.tag, "question": q.text, "reasons": reasons, "cost": cost, "body": body}
        )
    print(
        f"\nspent ${spent:.4f} of ${a.cap} (analysis ${analysis_usd:.4f}); "
        f"{len(results) - failures} pass, {failures} fail"
    )
    if a.json:
        Path(a.json).write_text(json.dumps(results, indent=1, default=str), encoding="utf-8")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
