"""Run the monitor once: snapshot, alert state update, delivery. For a scheduler outside the app (cron, launchd).

    uv run python backend/scripts/monitor_once.py            # evaluate and deliver
    uv run python backend/scripts/monitor_once.py --test     # send a labelled test alert through every sink
    uv run python backend/scripts/monitor_once.py --json     # print the result as JSON

Reads the same ``GLDB_*`` settings and database as the API. Set ``GLDB_MONITOR_INTERVAL_MINUTES`` to the
interval the scheduler runs at, and ``GLDB_MONITOR_IN_PROCESS=false`` on the API so it does not start its own
schedule: the silence check (``monitor_stale`` in ``GET /monitoring``) then watches this script.

Exit codes: 0 done; 1 the evaluation failed; 2 done, but an alert could not be delivered by a sink.
Output never contains a webhook URL, token or password.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
from collections.abc import Sequence
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.alerts import AlertService, Sink, set_meta  # noqa: E402
from app.core.config import Settings  # noqa: E402
from app.core.db import Database, ts  # noqa: E402
from app.core.models import utcnow  # noqa: E402


def run(
    settings: Settings,
    *,
    db: Database | None = None,
    test: bool = False,
    sinks: Sequence[Sink] | None = None,
) -> tuple[int, dict]:
    if db is None:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        db = Database(settings.database_path, data_dir=settings.data_dir)
    db.init_schema()
    service = AlertService(db, settings, clock=utcnow, sinks=sinks)
    try:
        if test:
            result = service.send_test(who="monitor_once")
            return (0 if result["delivered"] else 2), result
        result = service.evaluate(source="cli")
    except Exception as exc:  # noqa: BLE001 - report the class, never the message
        with contextlib.suppress(Exception):
            set_meta(db, "last_error", f"{type(exc).__name__} at {ts(utcnow())}", utcnow())
        return 1, {"error": type(exc).__name__}
    return (2 if result["delivery_failed"] else 0), result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--test", action="store_true", help="send a test alert instead of evaluating"
    )
    parser.add_argument("--json", action="store_true", help="print the full result as JSON")
    args = parser.parse_args(argv)
    code, result = run(Settings(), test=args.test)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    elif "error" in result:
        print(f"monitor failed: {result['error']}", file=sys.stderr)
    elif args.test:
        print(f"test alert: delivered={result['delivered']} sinks={result['sinks_configured']}")
    else:
        print(
            f"monitor run {result['evaluated_at']}: alerting={result['alerting']} "
            f"opened={result['opened']} resolved={result['resolved']} stale={result['monitor_stale']} "
            f"sinks={result['sinks']}"
        )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
