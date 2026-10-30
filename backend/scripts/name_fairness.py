"""Name fairness on a larger seeded DEV/TEST set and real Wikidata alias pairs (offline, no LLM).

    uv run python backend/scripts/name_fairness.py --out /tmp/nf
    uv run python -I backend/scripts/name_fairness.py --wikidata /path/to/wikidata_alias_pairs.csv

The 270-pair pipeline slice stays in ``eval.py --suite fairness``. Fictitious names; results are an
upper bound. See ``scripts/_eval/name_fairness.py`` for the design.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from scripts._eval import name_fairness as nf  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seed", type=int, default=nf.SEED)
    ap.add_argument("--wikidata", type=Path, help="wikidata_alias_pairs.csv (data file only)")
    ap.add_argument(
        "--legacy-names",
        type=Path,
        help="copy of the old app/names.py (git show REV:backend/app/names.py): the 'old' column "
        "then runs it; without it 'old' emulates only the missed-slip bug",
    )
    ap.add_argument("--out", type=Path, help="directory for name_fairness.json and .md")
    args = ap.parse_args(argv)
    if args.legacy_names:
        nf.MATCHERS["old"] = nf.load_legacy(args.legacy_names)
    synthetic = nf.run_synthetic(seed=args.seed)
    wikidata = nf.run_wikidata(args.wikidata) if args.wikidata else None
    text = nf.render_markdown(synthetic, wikidata)
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        payload = {"synthetic": synthetic, "wikidata": wikidata}
        (args.out / "name_fairness.json").write_text(
            json.dumps(payload, indent=1), encoding="utf-8"
        )
        (args.out / "name_fairness.md").write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
