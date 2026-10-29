"""No wall clock inside checks (architecture.md section 1): today comes from ``ctx.today``.

AST based: fails when anything under ``backend/app/checks/**`` calls ``date.today``,
``datetime.now``, ``datetime.utcnow`` or ``time.time`` (also via ``from time import time`` or
``datetime.datetime.now``).
"""

from __future__ import annotations

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
FORBIDDEN_ATTRS = {("date", "today"), ("datetime", "now"), ("datetime", "utcnow"), ("time", "time")}


def wallclock_calls(source: str) -> list[tuple[int, str]]:
    tree = ast.parse(source)
    bare: set[str] = set()  # names imported directly: `from time import time`
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in ("time", "datetime"):
            for alias in node.names:
                if (node.module, alias.name) in {("time", "time")}:
                    bare.add(alias.asname or alias.name)
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if isinstance(fn, ast.Attribute):
            owner = fn.value
            owner_name = (
                owner.id
                if isinstance(owner, ast.Name)
                else (owner.attr if isinstance(owner, ast.Attribute) else None)
            )
            if (owner_name, fn.attr) in FORBIDDEN_ATTRS:
                found.append((node.lineno, f"{owner_name}.{fn.attr}()"))
        elif isinstance(fn, ast.Name) and fn.id in bare:
            found.append((node.lineno, f"{fn.id}()"))
    return found


def test_detector_catches_each_forbidden_call() -> None:
    for snippet in (
        "import datetime\nx = datetime.date.today()",
        "from datetime import date\nx = date.today()",
        "from datetime import datetime\nx = datetime.now()",
        "import datetime\nx = datetime.datetime.utcnow()",
        "import time\nx = time.time()",
        "from time import time\nx = time()",
    ):
        assert wallclock_calls(snippet), snippet


def test_detector_allows_injected_today_and_monotonic_timers() -> None:
    ok = "import time\nfrom datetime import date\n\ndef check(ctx):\n    t = time.perf_counter()\n    return ctx.today > date(2020, 1, 1)\n"
    assert wallclock_calls(ok) == []


def scan_directory(directory: Path) -> list[str]:
    offenders = []
    for path in sorted(directory.rglob("*.py")):
        for lineno, call in wallclock_calls(path.read_text(encoding="utf-8")):
            offenders.append(f"{path.name}:{lineno}: {call}")
    return offenders


def test_a_scratch_check_with_date_today_fails_the_guard(tmp_path: Path) -> None:
    """Adding date.today() to a check module makes the directory scan report it."""
    (tmp_path / "scratch_check.py").write_text(
        "from datetime import date\n\ndef check(ctx):\n    return date.today()\n"
    )
    (tmp_path / "fine_check.py").write_text("def check(ctx):\n    return ctx.today\n")
    assert scan_directory(tmp_path) == ["scratch_check.py:4: date.today()"]


def test_no_wallclock_calls_under_app_checks() -> None:
    offenders = scan_directory(APP / "checks")
    assert offenders == [], "checks must use ctx.today, never the wall clock:\n" + "\n".join(
        offenders
    )


def test_scorer_modules_are_clock_free_too() -> None:
    offenders = []
    for name in ("findings.py", "score.py", "queue.py"):
        path = APP / name
        if path.exists():
            offenders += [f"{name}:{n}: {c}" for n, c in wallclock_calls(path.read_text())]
    assert offenders == []
