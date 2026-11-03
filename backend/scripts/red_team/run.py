"""Run the red-team attacks through the real pipeline (offline, fake LLM) and write the results.

    uv run python backend/scripts/red_team/run.py --out /tmp/red/results.json
    uv run python backend/scripts/red_team/run.py --only R01 R02 --out /tmp/x.json

Deterministic for a given ``--seed``. Nothing is sent to any hosted model.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[2]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

for _n in ("pyhanko", "pyhanko_certvalidator"):
    logging.getLogger(_n).setLevel(logging.CRITICAL)

from scripts.red_team import attacks  # noqa: E402,F401
from scripts.red_team.core import REGISTRY, SEED, run_all  # noqa: E402


def summarise(results) -> dict:
    """Counts per family: attacks, detected, evaded, silent; known-miss rows kept apart."""
    fams: dict[str, dict[str, int]] = {}
    for r in results:
        f = fams.setdefault(
            r.family,
            dict(attacks=0, detected=0, evaded=0, silent=0, consistent=0, sanity=0, error=0),
        )
        f["attacks"] += 1
        if r.verdict == "error":
            f["error"] += 1
        elif r.sanity:
            f["sanity"] += 1
        elif r.consistent:
            f["consistent"] += 1
        elif r.verdict == "detected":
            f["detected"] += 1
        else:
            f["evaded"] += 1
            f["silent"] += int(r.silent)
    return fams


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--seed", type=int, default=SEED)
    p.add_argument("--only", nargs="*")
    p.add_argument("--out", default=None)
    p.add_argument("--cache", default=None, help="where the clean bases are generated")
    a = p.parse_args(argv)
    cache = Path(a.cache) if a.cache else Path(tempfile.mkdtemp(prefix="red-cache-"))
    cache.mkdir(parents=True, exist_ok=True)
    results = run_all(
        sorted(REGISTRY, key=lambda x: x.id), cache, a.seed, set(a.only or []) or None
    )
    payload = {
        "seed": a.seed,
        "summary": summarise(results),
        "results": [asdict(r) for r in results],
    }
    if a.out:
        Path(a.out).write_text(json.dumps(payload, indent=1, sort_keys=True), encoding="utf-8")
    print(json.dumps(payload["summary"], indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
