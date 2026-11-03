"""Does each flag point at every place its evidence talks about?

    uv run python backend/scripts/audit_flag_places.py --out docs/flag-places.md

A flag marks a main place (``region``) and any related places (``extra_regions``). When its evidence quotes a
figure that is printed on the flagged documents but lies inside none of those places, the viewer cannot show it:
the audience sees a number in the evidence and no box on the page. This audit runs every red-team attack, the
sample scenarios and the showcase documents through the pipeline, takes every money figure quoted in each flag's
evidence, looks for it among the printed words of the flag's documents, and lists, per check, how often a quoted
figure that is printed on the page was not marked. Figures that are only computed (an expected balance) are
printed nowhere and are not places; they are ignored.
"""

from __future__ import annotations

import argparse
import collections
import random
import re
import shutil
import sys
import tempfile
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from scripts.audit_flag_locations import _run  # noqa: E402

MONEY = re.compile(r"\d[\d,]*\.\d{2}")
PAD = 0.012  # a figure whose centre is this close to a place counts as inside it


def _norm(text: str) -> str:
    return text.replace(",", "").strip("()$ ")


def unmarked(case) -> list[tuple[object, list[str]]]:  # noqa: ANN001
    """For each flag: the quoted figures that are printed on its documents but inside none of its places."""
    docs = {d.id: d for d in case.documents}
    out = []
    for flag in case.flags:
        places = ([flag.region] if flag.region else []) + list(flag.extra_regions)
        quoted = {_norm(t) for t in MONEY.findall(flag.evidence)}
        missing = []
        for fig in sorted(quoted):
            spots = []
            for did in flag.document_ids:
                d = docs.get(did)
                if d is None:
                    continue
                spots += [(d.file_id, w) for w in d.words if _norm(w.text) == fig]
            if not spots:
                continue  # computed, not printed
            inside = any(
                p.file_id == fid
                and p.page == w.page
                and p.x - PAD <= w.x + w.w / 2 <= p.x + p.w + PAD
                and p.y - PAD <= w.y + w.h / 2 <= p.y + p.h + PAD
                for fid, w in spots
                for p in places
            )
            if not inside:
                missing.append(fig)
        out.append((flag, missing))
    return out


def collect() -> dict[str, dict]:
    from types import SimpleNamespace

    from scripts import make_showcase_bundles as sb
    from scripts.red_team import attacks  # noqa: F401
    from scripts.red_team.core import REGISTRY, Runner

    stats: dict[str, dict] = collections.defaultdict(
        lambda: {"flags": 0, "with_figures": 0, "gaps": 0, "example": ""}
    )
    work = Path(tempfile.mkdtemp(prefix="places-audit-"))
    runner = Runner(work)

    def add(case) -> None:  # noqa: ANN001
        for flag, missing in unmarked(case):
            s = stats[flag.rule]
            s["flags"] += 1
            if MONEY.search(flag.evidence):
                s["with_figures"] += 1
            if missing:
                s["gaps"] += 1
                s["example"] = s["example"] or f"{', '.join(missing)} in: {flag.evidence[:110]}"

    try:
        for scenario in ("loan", "kyc", "aml"):
            add(_run(runner.base(scenario), work, f"base-{scenario}"))
        for a in REGISTRY:
            b = runner.base(a.scenario).copy()
            try:
                a.build(b, random.Random(f"4002:{a.id}"))
                add(_run(b, work, a.id))
            except Exception as exc:  # noqa: BLE001
                print(f"{a.id}: skipped ({type(exc).__name__})", file=sys.stderr)
        show = Path(tempfile.mkdtemp(prefix="show-", dir=work))
        for entry in sb.build(show / "out", 1):
            files = {Path(f).name: (show / "out" / f).read_bytes() for f in entry["files"]}
            add(
                _run(
                    SimpleNamespace(files=files, registry_path=None, scenario=entry["use_case"]),
                    work,
                    entry["slug"],
                )
            )
    finally:
        runner.close()
        shutil.rmtree(work, ignore_errors=True)
    return stats


def render(stats: dict[str, dict]) -> str:
    lines = [
        "| Check | Flags | Quoting a figure | Flags with a printed figure not marked | Example |",
        "|---|---|---|---|---|",
    ]
    for rule, s in sorted(stats.items()):
        lines.append(
            f"| `{rule}` | {s['flags']} | {s['with_figures']} | {s['gaps']} | {s['example'].replace('|', '/')} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path)
    a = ap.parse_args(argv)
    text = render(collect())
    if a.out:
        a.out.write_text(text)
        print(f"wrote {a.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
