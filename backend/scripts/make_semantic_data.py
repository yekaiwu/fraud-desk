"""Write the semantic evaluation data (docs/test-data-and-eval.md section 9).

    uv run python backend/scripts/make_semantic_data.py --out samples/semantic

Hand-written catalogues and code only: no model is called and nothing is downloaded. Seeded and
deterministic (dev 5101, held-out 7001). All data is fictitious, hand-written by us, and an upper
bound for any number measured on it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from scripts._eval import sem_data  # noqa: E402
from scripts._gen.sem_common import SEEDS  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=Path("samples/semantic"))
    ap.add_argument("--seed-dev", type=int, default=SEEDS["dev"])
    ap.add_argument("--seed-held-out", type=int, default=SEEDS["held_out"])
    args = ap.parse_args(argv)
    manifest = sem_data.write_all(args.out, {"dev": args.seed_dev, "held_out": args.seed_held_out})
    print(json.dumps(manifest["counts"], indent=2, sort_keys=True))
    print(f"wrote {len(manifest['files'])} files and manifest.json to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
