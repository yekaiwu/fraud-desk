"""Mock bundle generator CLI (docs/test-data-and-eval.md section 3.1).

    uv run python backend/scripts/make_bundle.py --scenario loan --variant all --out samples

Bundle builders are auto-discovered: every ``_gen/bundle_*.py`` that defines ``NAME`` and
``build(spec, out_dir)`` is registered, so new scenarios add a module and never edit this file.
Seeded and deterministic; never reads the wall clock (``--today`` defaults to 2026-10-27).
All data is fictitious.
"""

from __future__ import annotations

import argparse
import importlib
import json
import pkgutil
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from types import ModuleType

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from scripts import _gen  # noqa: E402
from scripts._gen import quirks as _quirks  # noqa: E402
from scripts._gen import registry_writer  # noqa: E402
from scripts._gen.schema import (  # noqa: E402
    FIXED_TODAY,
    BundleSpec,
    Manifest,
    bundle_id,
    dump_schemas,
)
from scripts._gen.verify import verify_bundle  # noqa: E402

SCENARIOS = ("loan", "kyc", "aml")
VARIANTS = ("clean", "fraud", "forger")
HARD_COMPANY = {"loan": 90, "kyc": 91, "aml": 92}


class BuilderNotAvailable(RuntimeError):
    """No ``_gen/bundle_<scenario>`` builder is installed yet."""


def discover_builders() -> dict[str, ModuleType]:
    """``NAME -> module`` for every ``_gen/bundle_*.py`` exposing ``NAME`` and ``build``."""
    found: dict[str, ModuleType] = {}
    for info in sorted(pkgutil.iter_modules(_gen.__path__), key=lambda m: m.name):
        if not info.name.startswith("bundle_"):
            continue
        mod = importlib.import_module(f"scripts._gen.{info.name}")
        name, build = getattr(mod, "NAME", None), getattr(mod, "build", None)
        if isinstance(name, str) and callable(build):
            found[name] = mod
    return found


def build_bundle(spec: BundleSpec, out_dir: Path) -> Manifest:
    """Dispatch on ``spec.scenario`` and write the bundle folder ``out_dir`` (docs 3.3)."""
    builders = discover_builders()
    mod = builders.get(spec.scenario)
    if mod is None:
        raise BuilderNotAvailable(
            f"builder for scenario '{spec.scenario}' not available yet (needs _gen/bundle_{spec.scenario}.py)"
        )
    return mod.build(spec, out_dir)


def build_registry(out_path: Path, manifests: Sequence[Manifest]) -> None:
    """``registry.json``: union of the entities of every manifest's company, sorted by uen."""
    registry_writer.build_registry(out_path, manifests)


__all__ = [
    "BuilderNotAvailable",
    "build_bundle",
    "build_registry",
    "discover_builders",
    "verify_bundle",
    "main",
]


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Generate fictitious mock bundles for the GLDB fraud tool."
    )
    p.add_argument("--scenario", default="loan", choices=[*SCENARIOS, "all"])
    p.add_argument("--variant", default="clean", choices=[*VARIANTS, "all"])
    p.add_argument("--company-index", type=int, default=0)
    p.add_argument("--seed", type=int, default=4002)
    p.add_argument(
        "--today",
        default=FIXED_TODAY.isoformat(),
        help="ISO date injected into the world (never the wall clock)",
    )
    p.add_argument("--out", default="samples")
    p.add_argument(
        "--render", default="digital", help="comma list of digital,scan_hq,scan_lq,photo,scan_pdf"
    )
    p.add_argument("--layout", choices=list(_gen.schema.LAYOUTS))
    p.add_argument("--packet", action="store_true")
    p.add_argument("--clean-pool", nargs="?", const=10, type=int, default=None, metavar="N")
    p.add_argument("--quirk-hard", action="store_true")
    p.add_argument("--gate-set", action="store_true")
    p.add_argument("--fairness", action="store_true")
    p.add_argument("--canaries", action="store_true")
    p.add_argument("--verify", metavar="DIR")
    p.add_argument("--dump-schemas", metavar="DIR")
    return p.parse_args(argv)


def _render_list(text: str) -> list[str]:
    out = [r.strip() for r in text.split(",") if r.strip()]
    bad = [r for r in out if r not in _gen.schema.RENDERS]
    if bad:
        raise SystemExit(f"unknown render(s): {', '.join(bad)}")
    return out


def _build_one(
    spec: BundleSpec, out_root: Path, tag: str = ""
) -> tuple[Manifest | None, list[str]]:
    bdir = out_root / "bundles" / bundle_id(spec)
    try:
        m = build_bundle(spec, bdir)
    except BuilderNotAvailable as exc:
        print(f"SKIP {bundle_id(spec)}: {exc}")
        return None, []
    # the registry must exist before verifying so the ownership/director checks (W8) can run
    build_registry(out_root / "registry.json", _all_manifests(out_root))
    problems = verify_bundle(bdir)
    status = "verified" if not problems else f"{len(problems)} invariant violation(s)"
    print(f"built {m.bundle_id}{tag}: {len(m.files)} files, {len(m.documents)} documents, {status}")
    for line in problems[:20]:
        print(f"  {line}")
    return m, problems


def _all_manifests(out_root: Path) -> list[Manifest]:
    manifests = []
    for path in sorted(out_root.glob("bundles/*/manifest.json")):
        manifests.append(Manifest.model_validate(json.loads(path.read_text(encoding="utf-8"))))
    return manifests


def main(argv: Sequence[str] | None = None) -> int:
    a = _parse_args(argv)
    if a.dump_schemas:
        for path in dump_schemas(Path(a.dump_schemas)):
            print(f"wrote {path}")
        return 0
    if a.verify:
        problems = verify_bundle(Path(a.verify))
        if problems:
            print("\n".join(problems))
            return 1
        print(f"{a.verify}: verified (W1-W14)")
        return 0
    today = date.fromisoformat(a.today)
    out_root = Path(a.out)
    renders = _render_list(a.render)
    scenarios = list(SCENARIOS) if a.scenario == "all" else [a.scenario]
    variants = list(VARIANTS) if a.variant == "all" else [a.variant]
    failed = False
    built = False
    base = dict(seed=a.seed, today=today, layout=a.layout, renders=renders, packet=a.packet)
    if a.clean_pool is not None:
        for sc in scenarios:
            for idx in range(1, a.clean_pool + 1):
                _, probs = _build_one(
                    BundleSpec(scenario=sc, variant="clean", company_index=idx, **base), out_root
                )
                failed |= bool(probs)
                built = True
    elif a.quirk_hard:
        for sc in scenarios:
            idx = HARD_COMPANY[sc]
            hard = _quirks.hard_quirks_for(sc)
            spec = BundleSpec(
                scenario=sc,
                variant="clean",
                company_index=idx,
                quirks=[*_quirks.default_quirks(idx), *hard],
                **base,
            )
            _, probs = _build_one(spec, out_root, " (hard quirks " + ",".join(hard) + ")")
            failed |= bool(probs)
            built = True
    elif not (a.gate_set or a.fairness or a.canaries):
        for sc in scenarios:
            for v in variants:
                spec = BundleSpec(scenario=sc, variant=v, company_index=a.company_index, **base)
                _, probs = _build_one(spec, out_root)
                failed |= bool(probs)
                built = True
    if built:
        manifests = _all_manifests(out_root)
        if manifests:
            build_registry(out_root / "registry.json", manifests)
            print(f"wrote {out_root / 'registry.json'} ({len(manifests)} bundle(s))")
    if a.gate_set:
        from scripts._gen import gate

        shortfalls = gate.build_gate_set(out_root, seed=a.seed, today=today)
        print(f"wrote {out_root / 'gate'}")
        for s in shortfalls:
            print(f"  shortfall: {s}")
    if a.canaries:
        from scripts._gen import canaries

        made = canaries.build_canaries(out_root, seed=a.seed, today=today)
        print(f"wrote {len(made)} canary bundle(s) under {out_root / 'canaries'}")
    if a.fairness:
        try:
            kf = importlib.import_module("scripts._gen.kyc_fairness")
        except ModuleNotFoundError:
            print("fairness set needs gen-kyc (scripts/_gen/kyc_fairness.py is not installed yet)")
        else:
            kf.build_fairness(out_root, seed=a.seed, today=today)
            print(f"wrote {out_root / 'fairness'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
