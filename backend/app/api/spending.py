"""Who spent what on the model today (``GET /spending``), for the accounts in ``GLDB_ADMIN_USERS``.

The figures come from the ledgers ``DailySpend`` keeps (``daily_spend`` for the site, ``daily_spend_user``
for each signed-in account), so they are dollars actually charged by paid backends. An analysis started
with no account (open mode, scripts) counts toward the site total only and shows as ``unattributed``.
"""

from __future__ import annotations

from datetime import UTC, timedelta
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Request

from app.api._common import DbDep, SettingsDep, get_clock, signed_in_reviewer
from app.core.costs import usd6
from app.core.errors import ApiError

router = APIRouter(tags=["spending"])

HISTORY_DAYS = 7


def _usd(micro: int) -> str:
    return format(usd6(Decimal(micro) / Decimal(1_000_000)), "f")


@router.get("/spending")
def spending(request: Request, db: DbDep, settings: SettingsDep) -> dict[str, Any]:
    who = signed_in_reviewer(request)
    if who is None or who not in settings.admin_user_set:
        raise ApiError(
            403, "forbidden", "Only an administrator account can see everybody's spending."
        )
    today = get_clock(request)().astimezone(UTC).date()
    since = (today - timedelta(days=HISTORY_DAYS - 1)).isoformat()
    with db.conn() as c:
        site = {
            r["day"]: int(r["micro_usd"])
            for r in c.execute("SELECT day, micro_usd FROM daily_spend WHERE day >= ?", (since,))
        }
        rows = c.execute(
            "SELECT day, account, micro_usd FROM daily_spend_user WHERE day >= ? ORDER BY micro_usd DESC",
            (since,),
        ).fetchall()
    today_key = today.isoformat()
    by_account_today = {r["account"]: int(r["micro_usd"]) for r in rows if r["day"] == today_key}
    week: dict[str, int] = {}
    for r in rows:
        week[r["account"]] = week.get(r["account"], 0) + int(r["micro_usd"])
    site_today = site.get(today_key, 0)
    accounts = sorted(set(by_account_today) | set(week))
    return {
        "viewer": who,
        "day": today_key,
        "site_cap_usd": format(settings.max_usd_per_day, "f"),
        "per_account_cap_usd": format(settings.max_usd_per_user_per_day, "f"),
        "site_today_usd": _usd(site_today),
        "unattributed_today_usd": _usd(max(0, site_today - sum(by_account_today.values()))),
        "accounts": sorted(
            (
                {
                    "account": a,
                    "today_usd": _usd(by_account_today.get(a, 0)),
                    "week_usd": _usd(week.get(a, 0)),
                }
                for a in accounts
            ),
            key=lambda x: (-Decimal(x["today_usd"]), -Decimal(x["week_usd"]), x["account"]),
        ),
        "history": [
            {"day": d, "site_usd": _usd(site.get(d, 0))}
            for d in ((today - timedelta(days=i)).isoformat() for i in range(HISTORY_DAYS))
        ],
    }
