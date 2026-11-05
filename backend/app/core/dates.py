"""Date arithmetic that cannot raise on a printed date at the edge of the calendar."""

from __future__ import annotations

from datetime import date, timedelta


def add_days(when: date, days: int) -> date:
    """``when`` plus ``days`` (negative to go back), clamped to ``date.min`` / ``date.max``.

    An open-ended contract that says "31 December 9999" would make ``when + timedelta(...)`` raise
    OverflowError and take the whole check down; as a clamped date it just means "no end"."""
    try:
        return when + timedelta(days=days)
    except OverflowError:
        return date.max if days > 0 else date.min
