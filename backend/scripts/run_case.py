"""Run a bundle (or loose files) through the real pipeline offline and print the findings.

    uv run python backend/scripts/run_case.py --scenario loan --variant clean
    uv run python backend/scripts/run_case.py --scenario loan --variant forger --timings
    uv run python backend/scripts/run_case.py --bundle samples/bundles/loan-fraud --json
    uv run python backend/scripts/run_case.py --files a.pdf b.pdf --use-case loan

``analyse_files`` is the one entry point the eval harness imports: it stores the files exactly as
``POST /cases`` does (``new_case`` + ``add_file`` + ``db.create_case``), then runs the pipeline
synchronously. ``today`` is always passed in (never the wall clock) and defaults to the bundle
manifest's ``spec.today`` or 2026-10-27. No network, no API key: the Fake LLM client is used unless
``GLDB_LLM_API_KEY`` is set. All data is fictitious.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.core.cases import add_file, new_case  # noqa: E402
from app.core.config import Settings  # noqa: E402
from app.core.db import Database  # noqa: E402
from app.core.llm import LLMClient  # noqa: E402
from app.core.models import Case, UseCase  # noqa: E402
from app.core.registry import MockRegistry, load_registry  # noqa: E402
from app.pipeline import run_pipeline  # noqa: E402

FIXED_TODAY = date(2026, 10, 27)
_SEV_RANK = {"high": 0, "medium": 1, "low": 2, "info": 3}


def analyse_files(
    paths: Sequence[str | Path],
    *,
    use_case: UseCase,
    applicant_name: str | None = None,
    applicant_uen: str | None = None,
    today: date,
    db: Database,
    settings: Settings,
    llm: LLMClient | None = None,
    registry: MockRegistry | None = None,
    reference: str | None = None,
) -> Case:
    """Store ``paths`` as a new case (same path as ``POST /cases``), run every stage, return it."""
    case = new_case(
        settings,
        use_case,
        reference=reference,
        applicant_name=applicant_name,
        applicant_uen=applicant_uen,
        today=today,
    )
    for p in paths:
        path = Path(p)
        add_file(case, db.files, name=path.name, data=path.read_bytes())
    db.create_case(case)
    return run_pipeline(case.id, db=db, settings=settings, llm=llm, registry=registry)


def bundle_files(bundle_dir: Path, render: str = "digital") -> list[Path]:
    """The manifest's ``upload_order`` for ``render`` (the manifest is the only index)."""
    manifest = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
    rels = manifest["upload_order"].get(render)
    if rels is None:
        raise SystemExit(f"bundle {bundle_dir} has no '{render}' render")
    return [(bundle_dir / rel).resolve() for rel in rels]


def bundle_registry(bundle_dir: Path) -> MockRegistry | None:
    manifest = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
    rel = manifest.get("registry_path")
    if not rel:
        return None
    path = (bundle_dir / rel).resolve()
    return load_registry(path) if path.exists() else None


def stage_table(case: Case) -> list[dict[str, object]]:
    return [
        {
            "stage": s.stage,
            "status": s.status,
            "seconds": round(s.seconds or 0.0, 3),
            "cost_usd": str(s.cost_usd),
            "detail": s.detail,
            "warnings": len(s.warnings),
        }
        for s in case.stages
    ]


def summary(case: Case) -> dict[str, object]:
    return {
        "case_id": case.id,
        "status": case.status,
        "use_case": case.use_case,
        "applicant": case.applicant.model_dump(mode="json"),
        "score": case.score.value if case.score else None,
        "band": case.score.band if case.score else None,
        "lane": case.lane,
        "coverage": case.coverage.model_dump(mode="json") if case.coverage else None,
        "partial": case.partial,
        "partial_reasons": list(case.partial_reasons),
        "top_finding": case.top_finding,
        "flags": len(case.flags),
        "findings": len(case.findings),
        "documents": len(case.documents),
        "stages": stage_table(case),
        "model_calls": len([c for c in case.costs if c.model != "fake"]),
    }


def format_report(case: Case, *, timings: bool = False) -> str:
    out: list[str] = []
    s = case.score
    out.append(
        f"case {case.id} ({case.use_case}) status={case.status} applicant="
        f"{case.applicant.name or '-'} [{case.applicant.uen or '-'}]"
    )
    if s is not None:
        out.append(
            f"score {s.value} band={s.band} lane={case.lane} partial={case.partial or s.partial}"
        )
    if case.coverage is not None:
        c = case.coverage
        out.append(f"coverage ran {c.ran}/{c.applicable} ({c.ratio:.2f})")
    for reason in case.partial_reasons:
        out.append(f"partial reason: {reason}")
    if case.top_finding:
        out.append(f"top finding: {case.top_finding}")
    if timings:
        out.append("")
        out.append(f"{'stage':<10} {'status':<7} {'seconds':>9} {'cost_usd':>10}  detail")
        total_s = 0.0
        for st in case.stages:
            total_s += st.seconds or 0.0
            out.append(
                f"{st.stage:<10} {st.status:<7} {st.seconds or 0.0:>9.3f} "
                f"{st.cost_usd!s:>10}  {dict(st.detail)}"
            )
        total_cost = sum((c.usd for c in case.costs), Decimal("0"))
        out.append(
            f"{'total':<10} {'':<7} {total_s:>9.3f} {total_cost!s:>10}  calls={len(case.costs)}"
        )
    out.append("")
    out.append("findings:")
    shown = 0
    for f in sorted(case.findings, key=lambda f: (_SEV_RANK.get(f.severity, 9), f.id)):
        if f.severity == "info":
            continue
        shown += 1
        out.append(f"  [{f.severity:<6}] {f.title}")
    if not shown:
        out.append("  (none above info)")
    out.append("")
    out.append("flags:")
    for fl in sorted(case.flags, key=lambda x: (_SEV_RANK.get(x.severity, 9), x.id)):
        out.append(f"  [{fl.severity:<6}] {fl.rule:<26} {fl.id}")
    skipped = [r for r in case.check_runs if r.status != "ran"]
    if skipped:
        out.append("")
        out.append(f"checks not run ({len(skipped)}):")
        for r in skipped:
            out.append(f"  {r.rule:<28} {r.status:<8} {r.reason or ''}")
    return "\n".join(out)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run a bundle through the GLDB pipeline offline.")
    p.add_argument("--bundle", help="bundle folder (contains manifest.json)")
    p.add_argument("--scenario", choices=["loan", "kyc", "aml"])
    p.add_argument("--variant", choices=["clean", "fraud", "forger"])
    p.add_argument("--samples", default="samples", help="samples root (default: samples)")
    p.add_argument("--render", default="digital")
    p.add_argument("--files", nargs="+", help="loose files instead of a bundle")
    p.add_argument("--use-case", choices=["loan", "kyc", "aml"])
    p.add_argument("--applicant-name")
    p.add_argument("--applicant-uen")
    p.add_argument("--today", help="ISO date; default is the bundle's spec.today, else 2026-10-27")
    p.add_argument("--data-dir", help="keep the SQLite db and files here (default: temp dir)")
    p.add_argument("--timings", action="store_true", help="print per-stage seconds and cost")
    p.add_argument("--json", action="store_true", help="print the full case as JSON")
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    a = _parse_args(argv)
    registry: MockRegistry | None = None
    bundle_dir: Path | None = None
    manifest: dict = {}
    if a.files:
        paths = [Path(f) for f in a.files]
        use_case = a.use_case or "loan"
    else:
        if a.bundle:
            bundle_dir = Path(a.bundle)
        elif a.scenario and a.variant:
            bundle_dir = Path(a.samples) / "bundles" / f"{a.scenario}-{a.variant}"
        else:
            raise SystemExit("give --bundle DIR, --scenario and --variant, or --files")
        if not (bundle_dir / "manifest.json").exists():
            raise SystemExit(
                f"{bundle_dir} has no manifest.json; build it with "
                f"`uv run python backend/scripts/make_bundle.py --scenario {a.scenario or 'loan'}"
                f" --variant {a.variant or 'clean'} --out {a.samples}`"
            )
        manifest = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
        paths = bundle_files(bundle_dir, a.render)
        use_case = a.use_case or manifest.get("use_case", "loan")
        registry = bundle_registry(bundle_dir)
        if a.applicant_name is None and a.applicant_uen is None:
            pass  # derived by the pipeline from the documents, as for a real upload
    if a.today:
        today = date.fromisoformat(a.today)
    else:
        spec_today = (manifest.get("spec") or {}).get("today")
        today = date.fromisoformat(spec_today) if spec_today else FIXED_TODAY

    data_dir = Path(a.data_dir) if a.data_dir else Path(tempfile.mkdtemp(prefix="gldb-run-"))
    settings = Settings(
        _env_file=None,
        data_dir=data_dir,
        db_path=str(data_dir / "gldb.sqlite3"),
    )
    db = Database(settings.database_path, data_dir=settings.data_dir)
    db.init_schema()
    case = analyse_files(
        paths,
        use_case=use_case,
        applicant_name=a.applicant_name,
        applicant_uen=a.applicant_uen,
        today=today,
        db=db,
        settings=settings,
        registry=registry,
    )
    case = db.load_case(case.id)
    if a.json:
        print(json.dumps(case.model_dump(mode="json"), indent=2, sort_keys=True))
    else:
        print(format_report(case, timings=a.timings))
    return 0 if case.status == "open" else 1


if __name__ == "__main__":
    sys.exit(main())
