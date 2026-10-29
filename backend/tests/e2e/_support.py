"""Shared helpers for the end-to-end tests (integration-loan). Not a test module.

Bundles are generated once per session into a temp ``samples`` root (``samples/`` itself is
gitignored and may not exist in CI), then run through the real pipeline offline with the Fake LLM.
``today`` is always passed in (``FIXED_TODAY``), never read from the wall clock.
"""

from __future__ import annotations

import atexit
import json
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.core.db import Database
from app.core.llm import FakeLLMClient
from app.core.models import Case, Flag
from app.core.registry import MockRegistry, load_registry
from app.core.testing import FIXED_TODAY
from scripts import make_bundle
from scripts._gen.schema import BundleSpec
from scripts.run_case import analyse_files, bundle_files, bundle_registry

SEVERITY_RANK = {"info": 0, "low": 1, "medium": 2, "high": 3}
BAND_RANK = {"low": 0, "medium": 1, "high": 2}


@dataclass
class Run:
    """One analysed bundle: the case plus the database/settings it lives in."""

    case: Case
    db: Database
    settings: Settings
    bundle_dir: Path
    manifest: dict[str, Any]
    llm: FakeLLMClient
    registry: MockRegistry | None
    paths: list[Path] = field(default_factory=list)

    def rules(self, *, min_severity: str = "info") -> set[str]:
        floor = SEVERITY_RANK[min_severity]
        return {f.rule for f in self.case.flags if SEVERITY_RANK[f.severity] >= floor}

    def flags(self, rule: str) -> list[Flag]:
        return [f for f in self.case.flags if f.rule == rule]


def make_settings(root: Path, **overrides: Any) -> Settings:
    return Settings(
        _env_file=None,
        data_dir=root / "data",
        db_path=str(root / "data" / "gldb.sqlite3"),
        llm_backend="fake",
        **overrides,
    )


def new_db(settings: Settings) -> Database:
    db = Database(settings.database_path, data_dir=settings.data_dir)
    db.init_schema()
    return db


def build_bundles(
    root: Path, *, scenario: str = "loan", variants: tuple[str, ...] = ("clean", "fraud", "forger")
) -> dict[str, Path]:
    """Generate ``variants`` of ``scenario`` under ``root/bundles`` and the shared registry."""
    manifests = []
    out: dict[str, Path] = {}
    for variant in variants:
        spec = BundleSpec(scenario=scenario, variant=variant)  # type: ignore[arg-type]
        bdir = root / "bundles" / f"{scenario}-{variant}"
        manifests.append(make_bundle.build_bundle(spec, bdir))
        out[variant] = bdir
    make_bundle.build_registry(root / "registry.json", manifests)
    return out


def temp_root(prefix: str = "gldb-e2e-") -> Path:
    """A temp folder removed when the test process exits."""
    path = Path(tempfile.mkdtemp(prefix=prefix))
    atexit.register(shutil.rmtree, path, ignore_errors=True)
    return path


def run_bundle(
    bdir: Path,
    *,
    tmp: Path | None = None,
    llm: FakeLLMClient | None = None,
    settings: Settings | None = None,
    db: Database | None = None,
    today: date = FIXED_TODAY,
    paths: list[Path] | None = None,
    **settings_overrides: Any,
) -> Run:
    """Analyse one bundle with a fresh database (or the ``db`` given, for cross-case tests)."""
    manifest = json.loads((bdir / "manifest.json").read_text(encoding="utf-8"))
    if settings is None:
        settings = make_settings(tmp or temp_root(), **settings_overrides)
    if db is None:
        db = new_db(settings)
    llm = llm if llm is not None else FakeLLMClient()
    registry = bundle_registry(bdir)
    files = paths if paths is not None else bundle_files(bdir)
    case = analyse_files(
        files,
        use_case=manifest.get("use_case", "loan"),
        today=today,
        db=db,
        settings=settings,
        llm=llm,
        registry=registry,
    )
    return Run(
        case=db.load_case(case.id),
        db=db,
        settings=settings,
        bundle_dir=bdir,
        manifest=manifest,
        llm=llm,
        registry=registry,
        paths=list(files),
    )


def unmet_expectations(case: Case, expected: dict[str, Any]) -> list[str]:
    """Compare a case with a manifest ``expected`` block (test-data-and-eval 3.4); [] = all met."""
    problems: list[str] = []
    fired: dict[str, int] = {}
    for f in case.flags:
        fired[f.rule] = max(fired.get(f.rule, 0), SEVERITY_RANK[f.severity])
    for entry in expected.get("must_fire", []):
        rules = entry["rule"] if isinstance(entry["rule"], list) else [entry["rule"]]
        floor = SEVERITY_RANK[entry.get("min_severity", "info")]
        if not any(fired.get(r, -1) >= floor for r in rules):
            problems.append(f"must_fire {rules} at >= {entry.get('min_severity')}: not met")
    for rule in expected.get("must_not_fire", []):
        if rule in fired and fired[rule] > 0:
            problems.append(f"must_not_fire {rule}: fired")
    band = case.score.band if case.score else None
    if expected.get("lane") and case.lane != expected["lane"]:
        problems.append(f"lane {case.lane} != {expected['lane']}")
    if expected.get("lane_any_of") and case.lane not in expected["lane_any_of"]:
        problems.append(f"lane {case.lane} not in {expected['lane_any_of']}")
    if expected.get("band_min") and (
        band is None or BAND_RANK[band] < BAND_RANK[expected["band_min"]]
    ):
        problems.append(f"band {band} below {expected['band_min']}")
    if expected.get("band_max") and (
        band is None or BAND_RANK[band] > BAND_RANK[expected["band_max"]]
    ):
        problems.append(f"band {band} above {expected['band_max']}")
    limit = expected.get("max_flags_ge_medium")
    if limit is not None:
        n = sum(1 for f in case.flags if SEVERITY_RANK[f.severity] >= SEVERITY_RANK["medium"])
        if n > limit:
            problems.append(f"{n} flags >= medium (max {limit})")
    return problems


def text_under(run: Run, region: Any) -> str:
    """The printed text under a flag ``Region`` (fractions of the page, origin top-left), read from
    the stored original: proves the highlight lands on the cell the flag talks about."""
    import pymupdf

    file = next(f for f in run.case.files if f.id == region.file_id)
    doc = pymupdf.open(stream=run.db.files.read(file.sha256), filetype="pdf")
    page = doc[region.page - 1]
    w, h = page.rect.width, page.rect.height
    rect = pymupdf.Rect(
        region.x * w - 1,
        region.y * h - 1,
        (region.x + region.w) * w + 1,
        (region.y + region.h) * h + 1,
    )
    return " ".join(
        word[4] for word in page.get_text("words") if pymupdf.Rect(word[:4]).intersects(rect)
    )


class PricedFake(FakeLLMClient):
    """A Fake client that also books a priced cost per fresh call (the plain Fake costs 0), so the
    dollar guard and cache savings can be exercised offline. Answers with an empty page by default."""

    def __init__(self, usd: str = "1.50", **kwargs: Any):
        from decimal import Decimal

        kwargs.setdefault("default", EMPTY_STATEMENT_PAGE)
        super().__init__(**kwargs)
        self.usd = Decimal(usd)

    def complete_json(self, req, *, ctx_costs=None):  # noqa: ANN001, ANN201
        res = super().complete_json(req, ctx_costs=ctx_costs)
        if ctx_costs is not None and not res.cache_hit:
            ctx_costs.record(
                purpose=req.purpose,
                model="priced",
                prompt_tokens=100,
                completion_tokens=10,
                seconds=0.0,
                usd=self.usd,
            )
        return res


EMPTY_STATEMENT_PAGE: dict[str, Any] = {
    "fields": {
        k: None
        for k in [
            "bank_name",
            "account_holder",
            "uen",
            "account_number",
            "period_start",
            "period_end",
            "opening_balance",
            "closing_balance",
            "currency",
            "page_x_of_y",
            "total_debits",
            "total_credits",
        ]
    },
    "tables": {"transactions": [], "page_chain": []},
    "page_marker": None,
}


def tampered_loan_bundle(
    root: Path, name: str, months: tuple[str, ...], *, factor: str = "1.25"
) -> Path:
    """A clean loan bundle where, in each of ``months``, the largest deposit is raised by
    ``factor`` with balances left alone (T01). Returns the bundle folder under ``root``."""
    from decimal import Decimal

    from scripts._gen.schema import Mutation

    base = root / "bundles" / "loan-clean"
    if not (base / "manifest.json").exists():
        build_bundles(root, variants=("clean",))
    truth = json.loads((base / "ground_truth.json").read_text(encoding="utf-8"))
    muts = []
    for month in months:
        key = f"bank_statement:{month}"
        stmt = next(d for d in truth["documents"] if d["key"] == key)
        credits = [
            (r["index"], Decimal(r["cells"]["credit"]["value"]))
            for r in stmt["tables"]["transactions"]["rows"]
            if r["cells"]["credit"]["value"]
        ]
        index, amount = max(credits, key=lambda c: c[1])
        value = (amount * Decimal(factor)).quantize(Decimal("0.01"))
        muts.append(
            Mutation(
                op="override_cell",
                target=key,
                args={
                    "table": "transactions",
                    "row": index,
                    "column": "credit",
                    "value": str(value),
                },
            )
        )
    spec = BundleSpec(scenario="loan", variant="clean", mutations=muts)
    bdir = root / "bundles" / name
    make_bundle.build_bundle(spec, bdir)
    return bdir


def copy_bundle_files(bdir: Path, dest: Path) -> list[Path]:
    """Copy a bundle's upload files into ``dest`` (a flat folder) and return the copies."""
    dest.mkdir(parents=True, exist_ok=True)
    out = []
    for p in bundle_files(bdir):
        target = dest / p.name
        shutil.copyfile(p, target)
        out.append(target)
    return out


__all__ = [
    "EMPTY_STATEMENT_PAGE",
    "PricedFake",
    "Run",
    "build_bundles",
    "copy_bundle_files",
    "load_registry",
    "make_settings",
    "new_db",
    "run_bundle",
    "temp_root",
    "text_under",
    "tampered_loan_bundle",
    "unmet_expectations",
]
