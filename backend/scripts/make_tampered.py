"""Tamper catalogue CLI and ``apply_tamper`` (docs/test-data-and-eval.md sections 3.1, 3.2, 4.3).

    uv run python backend/scripts/make_tampered.py --list
    uv run python backend/scripts/make_tampered.py --tamper T01 T02a --n 5 --render digital,scan_hq
    uv run python backend/scripts/make_tampered.py --all --n 2

Each tampered case folder ``<out>/<tamper_id>-<base_bundle_id>-i<k>/`` holds ``manifest.json``
(with a ``TamperRecord``: type, expected rule, original and edited values, edited row/field/box),
``labels.json``, ``ground_truth.json`` and ONLY the files that changed; every other file is listed in
the manifest as ``inherited`` with a relative path into the clean base bundle. Instance ``k`` uses the
clean base of company ``k mod P``. Never reads the wall clock. All data is fictitious.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from scripts._eval import tamper_bundle, tamper_image, tamper_metadata, tamper_pdf, tamper_world  # noqa: E402
from scripts._eval.common import (  # noqa: E402
    DEFAULT_SEED,
    ensure_bundle,
    load_manifest,
)
from scripts._eval.tamper_core import (  # noqa: E402
    Ctx,
    Plan,
    TamperNotApplicable,
    TmpBundle,
    build_world_bundle,
    finalise,
    make_ctx,
)
from scripts._eval.tamper_defs import (  # noqa: E402
    TAMPERS,
    TamperDef,
    registered_rules,
    unregistered_rules,
)
from scripts._gen.schema import FIXED_TODAY, Manifest  # noqa: E402

BUILDERS = {
    **tamper_world.WORLD_BUILDERS,
    **tamper_bundle.BUNDLE_BUILDERS,
    **tamper_metadata.METADATA_BUILDERS,
    **tamper_pdf.PDF_BUILDERS,
    **tamper_image.IMAGE_BUILDERS,
}
CLEAN_POOL = 10
NOT_BUILT = {
    "T56b": "not built: the generator renders an ID card only for directors listed on the profile, so an ID for a non-director cannot be generated (generator gap)",
}
__all__ = ["TAMPERS", "TamperDef", "TamperNotApplicable", "apply_tamper", "main", "plan_for"]


def plan_for(ctx: Ctx) -> Plan:
    builder = BUILDERS.get(ctx.td.id)
    if builder is None:
        raise TamperNotApplicable(
            NOT_BUILT.get(ctx.td.id, f"{ctx.td.id} has no builder (not built)")
        )
    return builder(ctx)


def apply_tamper(
    base: Manifest,
    tamper_id: str,
    *,
    seed: int,
    instance: int,
    out_dir: Path,
    render: str = "digital",
    renders: Sequence[str] | None = None,
    base_dir: Path | None = None,
) -> Manifest:
    """Apply ``tamper_id`` to the clean ``base`` bundle and write ``out_dir``.

    ``renders`` lists every render to produce (default: digital plus ``render``). ``base_dir`` is the
    folder of the base manifest (default ``<out_dir>/../../bundles/<base id>``).
    """
    base_dir = base_dir or (out_dir.parent.parent / "bundles" / base.bundle_id)
    wanted = list(dict.fromkeys(["digital", render, *(renders or [])]))
    ctx = make_ctx(
        base, base_dir, tamper_id, seed=seed, instance=instance, render=render, renders=wanted
    )
    plan = plan_for(ctx)
    tmp = Path(tempfile.mkdtemp(prefix=f"gldb-tamper-{tamper_id}-"))
    try:
        if plan.mutations or plan.hook:
            spec = base.spec.model_copy(
                update={"mutations": [*base.spec.mutations, *plan.mutations], "renders": wanted}
            )
            build_world_bundle(spec, tmp, plan.hook)
        else:
            missing = [r for r in wanted if r != "digital" and r not in base.upload_order]
            if missing:
                raise TamperNotApplicable(f"base bundle lacks render(s) {missing}")
            shutil.copytree(
                base_dir, tmp, dirs_exist_ok=True, ignore=shutil.ignore_patterns("packet")
            )
        if plan.file_edit is not None:
            tb = TmpBundle(tmp)
            plan.file_edit(tb)
            tb.save()
        tb = TmpBundle(tmp)
        if plan.locate is None:  # pragma: no cover - every builder supplies a locator
            raise TamperNotApplicable(f"{tamper_id}: builder has no locator")
        target, original, edited = plan.locate(ctx.gt, tb.gt)
        bundle_id = f"{tamper_id}-{base.bundle_id}-i{instance}"
        return finalise(tmp, ctx, plan, out_dir, bundle_id, target, original, edited)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def base_company(instance: int, pool: int = CLEAN_POOL) -> int:
    return instance % pool


def build_instances(
    tamper_id: str,
    *,
    n: int,
    seed: int,
    samples: Path,
    out_root: Path,
    renders: Sequence[str],
    scenario: str | None = None,
    today=FIXED_TODAY,  # noqa: ANN001
    log=print,  # noqa: ANN001
) -> list[tuple[int, str, Path]]:
    """Build up to ``n`` instances of ``tamper_id`` for every applicable scenario (or ``scenario``).
    Returns ``(instance, scenario, tampered dir)``; inapplicable instances are logged and skipped."""
    td = TAMPERS[tamper_id]
    out: list[tuple[int, str, Path]] = []
    need = [r for r in renders if r in td.renders] or list(td.renders)
    for sc in td.scenarios:
        if scenario and sc != scenario:
            continue
        for k in range(n):
            bdir = ensure_bundle(
                samples,
                sc,
                "clean",
                base_company(k),
                seed=seed,
                today=today,
                renders=tuple(need),
                update_registry=True,
            )
            base = load_manifest(bdir / "manifest.json")
            dest = out_root / f"{tamper_id}-{base.bundle_id}-i{k}"
            try:
                apply_tamper(
                    base,
                    tamper_id,
                    seed=seed,
                    instance=k,
                    out_dir=dest,
                    render=need[0],
                    renders=need,
                    base_dir=bdir,
                )
            except TamperNotApplicable as exc:
                log(f"skip {tamper_id} i{k} ({sc}): {exc}")
                continue
            out.append((k, sc, dest))
    return out


def _parse(argv: Sequence[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build tampered copies of clean mock bundles.")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--tamper", nargs="+", metavar="ID")
    g.add_argument("--all", action="store_true")
    g.add_argument("--list", action="store_true")
    g.add_argument(
        "--canaries",
        action="store_true",
        help="build the injection canaries (3 hosts x 4 placements plus controls) under <samples>/canaries",
    )
    p.add_argument("--n", type=int, default=5)
    p.add_argument("--seed", type=int, default=DEFAULT_SEED)
    p.add_argument(
        "--render",
        default="digital",
        help="comma list: digital,scan_hq,scan_lq,photo (the degraded copies: world edits are applied before degradation, image edits after)",
    )
    p.add_argument("--out", default="samples/tampered")
    p.add_argument("--samples", default="samples")
    return p.parse_args(argv)


def format_catalogue() -> str:
    reg = registered_rules()
    lines = [
        f"{'id':<6} {'chan':<9} {'scn':<6} {'renders':<8} {'flags':<11} expected (any of)  | edit"
    ]
    for t in TAMPERS.values():
        flags = (
            ",".join(
                f
                for f, on in (("hard", t.hard), ("known_miss", t.known_miss), ("weak", t.weak))
                if on
            )
            or "-"
        )
        gap = unregistered_rules(t, reg) if reg else []
        exp = ", ".join(t.expected_any_of) or "(none)"
        if gap:
            exp += f"  [rule not implemented: gap {gap}]"
        built = "" if t.id in BUILDERS else "  [no builder]"
        scn = "".join(s[0].upper() for s in t.scenarios)
        rnd = "D" if "digital" in t.renders else ""
        rnd += "S" if any(r != "digital" for r in t.renders) else ""
        lines.append(
            f"{t.id:<6} {t.channel:<9} {scn:<6} {rnd:<8} {flags:<11} {exp} | {t.edit}{built}"
        )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    a = _parse(argv)
    if a.list:
        print(format_catalogue())
        return 0
    if a.canaries:
        from scripts._gen import canaries as gen_canaries

        made = gen_canaries.build_canaries(Path(a.samples), seed=a.seed, today=FIXED_TODAY)
        print(f"built {len(made)} canary bundle(s) under {Path(a.samples) / 'canaries'}")
        return 0
    ids = list(TAMPERS) if a.all else a.tamper
    bad = [i for i in ids if i not in TAMPERS]
    if bad:
        raise SystemExit(f"unknown tamper id(s): {', '.join(bad)} (try --list)")
    renders = [r.strip() for r in a.render.split(",") if r.strip()]
    samples, out_root = Path(a.samples), Path(a.out)
    reg = registered_rules()
    built = skipped = 0
    for tid in ids:
        gap = unregistered_rules(TAMPERS[tid], reg) if reg else []
        if gap:
            print(f"{tid}: rule not implemented: gap {gap}")
            skipped += 1
            continue
        got = build_instances(
            tid, n=a.n, seed=a.seed, samples=samples, out_root=out_root, renders=renders
        )
        built += len(got)
        print(f"{tid}: built {len(got)} instance(s)")
    print(f"built {built} tampered case(s) under {out_root}; {skipped} tamper(s) skipped as gaps")
    return 0


if __name__ == "__main__":
    sys.exit(main())
