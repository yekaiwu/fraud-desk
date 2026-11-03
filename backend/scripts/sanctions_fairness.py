"""False positives of sanctions screening by name group (offline once a snapshot exists).

    uv run python backend/scripts/sanctions_fairness.py --snapshot data/sanctions/current.json --out /tmp/sf
    uv run python backend/scripts/sanctions_fairness.py --snapshot ... --write-fixture backend/tests/data/sanctions_fairness_fixture.json

See ``scripts/_eval/sanctions_fairness.py`` and docs/verification-sources.md. Fictitious customers and random
draws of real list entries; Wilson 95% intervals; no network.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.sources.sanctions import Screener, load_snapshot  # noqa: E402
from scripts._eval import sanctions_fairness as sf  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--snapshot", type=Path, required=True)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--write-fixture", type=Path)
    args = ap.parse_args(argv)
    snap = load_snapshot(args.snapshot)
    if args.write_fixture:
        fixture = sf.build_fixture(snap)
        args.write_fixture.write_text(
            json.dumps(fixture, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
        )
        print(json.dumps(sf.measure_fixture(fixture), indent=1))
        return 0
    sc = Screener(snap)
    result = {
        "snapshot_built_at": snap.built_at.isoformat(),
        "entries": len(snap.entries),
        "fictitious": sf.screen_groups(sc, sf.fictitious_customers("test")),
        "real_draws": sf.screen_real(sc, sf.real_draws(snap)),
        "recall": sf.recall_probe(sc),
    }
    text = json.dumps(result, indent=1, ensure_ascii=False)
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "sanctions_fairness.json").write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
