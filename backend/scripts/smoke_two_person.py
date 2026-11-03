"""Live check of the two-person rule through the front door, with the demo's two real logins.

    uv run python backend/scripts/smoke_two_person.py [--base http://127.0.0.1:3000] [--env-file PATH]

Signs in as the shared login (``GLDB_SITE_USERNAME`` / ``GLDB_SITE_PASSWORD``) and as the first account of
``GLDB_SITE_USERS``, both read from the production env file, so no password is typed here, printed or kept in
this repository. It then uploads one small fictitious invoice that carries a high flag, has the first account
dismiss that flag, and checks that

* the dismissal waits for a second reviewer,
* the same account cannot countersign it, whatever reviewer name it types,
* the other account can, and the flag ends up dismissed,

and deletes the case again. Costs about one cent of model use. Exits 0 when all of that holds, 1 otherwise.
"""

from __future__ import annotations

import argparse
import http.cookiejar
import io
import json
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import date
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

DEFAULT_ENV = Path("~/.local/gldb-fraud/deploy/.env.production").expanduser()


def read_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in path.read_text().splitlines():
        m = re.match(r"^([A-Z0-9_]+)=(.*)$", line)
        if m:
            env[m.group(1)] = m.group(2).strip().strip("'\"")
    return env


def accounts(env: dict[str, str]) -> tuple[tuple[str, str], tuple[str, str]]:
    """(first account, second account) as (username, password)."""
    first = (env["GLDB_SITE_USERNAME"].strip().lower(), env["GLDB_SITE_PASSWORD"])
    users = json.loads(env.get("GLDB_SITE_USERS", "{}") or "{}")
    if not users:
        raise SystemExit(
            "GLDB_SITE_USERS lists no second account: the two-person rule cannot be shown"
        )
    name, password = next(iter(users.items()))
    return first, (name.strip().lower(), password)


class Session:
    def __init__(self, base: str, username: str, password: str):
        self.base = base.rstrip("/")
        self.username = username
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )
        status, _ = self.call(
            "POST", "/api/auth/login", {"username": username, "password": password}
        )
        if status != 200:
            raise SystemExit(f"sign-in as {username} failed ({status})")

    def call(self, method: str, path: str, body=None, raw: tuple[bytes, str] | None = None):  # noqa: ANN001, ANN201
        data, ctype = (
            raw
            if raw
            else (
                (json.dumps(body).encode(), "application/json")
                if body is not None
                else (None, None)
            )
        )
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if ctype:
            req.add_header("content-type", ctype)
        try:
            with self.opener.open(req, timeout=120) as r:
                payload = r.read()
                return r.status, (json.loads(payload) if payload else None)
        except urllib.error.HTTPError as e:
            text = e.read().decode()[:500]
            try:
                return e.code, json.loads(text)
            except ValueError:
                return e.code, text


def invoice_with_a_high_flag() -> bytes:
    """A fictitious invoice whose supplier UEN is not a valid UEN: ``inv_ids`` raises it as high."""
    from decimal import Decimal

    from scripts._gen.render_invoice import render_invoice
    from scripts._gen.world_types import InvLine, Invoice, Party

    n = uuid.uuid4().int % 9000 + 1000
    supplier = Party(
        name="Smoke Test Supplies Pte. Ltd.",
        uen="199999999Z",
        gst_no="M90000000A",
        address="1 Test Road, Singapore 000001",
    )
    buyer = Party(
        name="Smoke Test Buyer Pte. Ltd.", uen="201912345K", address="2 Test Road, Singapore 000002"
    )
    line = InvLine(
        description="Test item",
        quantity=Decimal("2"),
        unit_price=Decimal("50.00"),
        amount=Decimal("100.00"),
    )
    inv = Invoice(
        number=f"SMK-{n}", date=date(2026, 10, 1), due=date(2026, 10, 31), supplier=supplier, buyer=buyer, lines=[line],
        subtotal=Decimal("100.00"), gst_rate=Decimal("9"), gst_amount=Decimal("9.00"), total=Decimal("109.00"),
    )  # fmt: skip
    return render_invoice(inv).pdf


def multipart(fields: dict[str, str], name: str, data: bytes) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    out = io.BytesIO()
    for k, v in fields.items():
        out.write(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
        )
    out.write(
        f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="{name}"\r\n'
        "Content-Type: application/pdf\r\n\r\n".encode()
        + data
        + b"\r\n"
    )
    out.write(f"--{boundary}--\r\n".encode())
    return out.getvalue(), f"multipart/form-data; boundary={boundary}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", default="http://127.0.0.1:3000")
    ap.add_argument("--env-file", type=Path, default=DEFAULT_ENV)
    a = ap.parse_args(argv)
    (u1, p1), (u2, p2) = accounts(read_env(a.env_file))
    first, second = Session(a.base, u1, p1), Session(a.base, u2, p2)
    ok = True

    def check(label: str, condition: bool, detail: object = "") -> None:
        nonlocal ok
        ok &= bool(condition)
        print(f"{'ok  ' if condition else 'FAIL'} {label}" + ("" if condition else f"  [{detail}]"))

    me1, me2 = first.call("GET", "/api/me")[1], second.call("GET", "/api/me")[1]
    check(f"{u1} is signed in as {u1}", me1 == {"reviewer": u1}, me1)
    check(f"{u2} is signed in as {u2}", me2 == {"reviewer": u2}, me2)

    body, ctype = multipart(
        {"use_case": "loan", "reference": "smoke-two-person"},
        "invoice.pdf",
        invoice_with_a_high_flag(),
    )
    status, made = first.call("POST", "/api/cases", raw=(body, ctype))
    if status != 202:
        print("FAIL upload", status, made)
        return 1
    case_id, job_id = made["case_id"], made["job_id"]
    try:
        for _ in range(120):
            _, job = first.call("GET", f"/api/jobs/{job_id}")
            if job.get("state") in ("succeeded", "failed"):
                break
            time.sleep(2)
        _, case = first.call("GET", f"/api/cases/{case_id}")
        case = case.get("case", case)
        highs = [f for f in case["flags"] if f["severity"] == "high"]
        check(
            "the test invoice produced a high flag", bool(highs), [f["rule"] for f in case["flags"]]
        )
        if not highs:
            return 1
        flag_id, version = highs[0]["id"], case["version"]

        def review(who: Session, **b: object):  # noqa: ANN202
            return who.call(
                "POST",
                f"/api/cases/{case_id}/review",
                {"case_version": version, "reason": "smoke test of the two-person rule", **b},
            )

        def state() -> str:
            c = first.call("GET", f"/api/cases/{case_id}")[1]
            c = c.get("case", c)
            return next(f["disposition"] for f in c["flags"] if f["id"] == flag_id)

        st, res = review(
            first,
            kind="flag_decision",
            decision="dismiss",
            reviewer="typed name",
            flag_ids=[flag_id],
        )
        entry = (res or {}).get("entry", {})
        check(
            f"{u1} dismisses the high flag: it waits for a second reviewer",
            st == 201 and entry.get("status") == "pending_second_approval",
            (st, res),
        )
        check(
            "the flag now reads pending_second_approval",
            state() == "pending_second_approval",
            state(),
        )
        check(
            f"the reviewer recorded is {u1}, not the typed name",
            entry.get("reviewer") == u1,
            entry.get("reviewer"),
        )

        for typed in (u1, u1.upper(), u2):
            st, res = review(
                first,
                kind="second_approval",
                decision="approve",
                reviewer=typed,
                target_entry_id=entry.get("id"),
            )
            code = (res or {}).get("error", {}).get("code") if isinstance(res, dict) else None
            check(
                f"{u1} cannot countersign himself (typing {typed!r})",
                st == 409 and code == "second_approver_must_differ",
                (st, res),
            )
        check("still pending", state() == "pending_second_approval", state())

        st, res = review(
            second,
            kind="second_approval",
            decision="approve",
            reviewer="typed name",
            target_entry_id=entry.get("id"),
        )
        check(
            f"{u2} countersigns",
            st == 201 and (res or {}).get("entry", {}).get("reviewer") == u2,
            (st, res),
        )
        check("the flag is dismissed", state() == "dismissed", state())
    finally:
        st, _ = first.call(
            "DELETE", f"/api/cases/{case_id}?reviewer=smoke&reason=two+person+smoke+test+finished"
        )
        print(f"{'ok  ' if st == 204 else 'WARN'} test case removed ({st})")
    print("ALL GOOD" if ok else "SOMETHING FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
