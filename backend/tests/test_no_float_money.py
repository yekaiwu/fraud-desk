"""No ``float`` for money (plan 8.5, architecture.md section 10): amounts are ``Decimal``.

AST based over ``backend/app/**``: fails when a name that holds money (amount, balance, debit,
credit, total, price, usd, dollar) is annotated ``float`` or assigned from ``float(...)``, or when a
``float(...)`` call wraps such a name. A ratio of two amounts (``float(a / b)``), probabilities, scores and page fractions stay floats.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
MONEY = re.compile(
    r"(amount|balance|debit|credit|price|\busd|usd\b|_usd|usd_|dollar|money|payable|turnover)", re.I
)


def _names(node: ast.AST) -> list[str]:
    out: list[str] = []
    for n in ast.walk(node):
        if isinstance(n, ast.Name):
            out.append(n.id)
        elif isinstance(n, ast.Attribute):
            out.append(n.attr)
    return out


def _is_float(ann: ast.AST | None) -> bool:
    return ann is not None and any(
        isinstance(n, ast.Name) and n.id == "float" for n in ast.walk(ann)
    )


def float_money(source: str) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.AnnAssign) and _is_float(node.annotation):
            target = ast.unparse(node.target)
            if MONEY.search(target):
                found.append((node.lineno, f"{target}: float"))
        elif isinstance(node, ast.arg) and _is_float(node.annotation) and MONEY.search(node.arg):
            found.append((node.lineno, f"argument {node.arg}: float"))
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "float"
        ):
            ratio = any(isinstance(a, ast.BinOp) and isinstance(a.op, ast.Div) for a in node.args)
            if not ratio and any(MONEY.search(n) for a in node.args for n in _names(a)):
                found.append((node.lineno, "float(" + ast.unparse(node.args[0]) + ")"))
    return found


def test_detector_catches_float_money() -> None:
    assert float_money("x: float = 1.0\nbalance: float = 0.0")
    assert float_money("def f(amount: float): ...")
    assert float_money("y = float(row.debit)")
    assert not float_money("ratio: float = 0.5\nz = float(page.rect.width)")
    assert not float_money("share = float(credit_total / all_total)")  # a ratio, not money
    assert not float_money("balance: Decimal = Decimal('1.00')")


def test_no_float_for_money_under_app() -> None:
    offenders = []
    for path in sorted(APP.rglob("*.py")):
        offenders += [
            f"{path.relative_to(APP)}:{n}: {c}"
            for n, c in float_money(path.read_text(encoding="utf-8"))
        ]
    assert offenders == [], "money must be Decimal, never float:\n" + "\n".join(offenders)
