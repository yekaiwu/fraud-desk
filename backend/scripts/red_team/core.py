"""Red-team harness core: bundles, attacks, the runner and the verdict logic.

An attack is a function that takes a fresh copy of a clean bundle (:class:`Bundle`) and a seeded
``random.Random`` and edits it, the way a forger would, through :mod:`pdfkit` / :mod:`models` or
plain file operations. The runner then pushes the forged bundle through the real pipeline
(``run_case.analyse_files``, fake LLM, semantic checks off, no network) and compares the result
with the same pipeline run on the clean base (the control).

Verdicts
--------
* ``detected``  - at least one medium or high flag that is not in the control run (same rule, same
  files) fires. Noise already present on the clean base is never credited.
* ``evaded``    - no such flag. ``silent`` evasions also raise nothing above info and keep the
  control's lane, so a reviewer would see a clean fast-lane case.
* ``consistent`` attacks (the forger recomputed every figure the checks look at) are the documented
  known miss; they are tabulated but never counted as discoveries.

All documents are fictitious; nothing leaves the machine.
"""

from __future__ import annotations

import json
import random
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[2]

SEED = 20261103
TODAY = date(2026, 10, 27)
SCENARIOS = ("loan", "kyc", "aml")
USE_CASE = {"loan": "loan", "kyc": "kyc", "aml": "aml"}


@dataclass
class Bundle:
    """A mutable copy of a clean bundle: ordered files, registry path, manifest."""

    scenario: str
    files: dict[str, bytes]  # upload order, keys are file names
    registry_path: Path | None
    manifest: dict

    def copy(self) -> Bundle:
        return Bundle(self.scenario, dict(self.files), self.registry_path, self.manifest)

    def names(self, prefix: str) -> list[str]:
        return sorted(n for n in self.files if n.startswith(prefix))

    def add(self, name: str, data: bytes) -> None:
        if name in self.files:
            raise ValueError(f"{name} exists")
        self.files[name] = data

    def replace(self, name: str, data: bytes) -> None:
        if name not in self.files:
            raise KeyError(name)
        self.files[name] = data

    def remove(self, name: str) -> None:
        del self.files[name]


@dataclass
class Outcome:
    """What an attack reports about itself."""

    claim: str  # what the forger did and wanted
    targets: list[str] = field(default_factory=list)  # file names carrying the forgery
    must_read: list[str] = field(default_factory=list)  # strings the pipeline should read
    must_see: list[tuple[str, str]] = field(default_factory=list)  # (file, string) a human sees
    notes: str = ""


@dataclass
class Attack:
    id: str
    family: str
    title: str
    scenario: str
    build: Callable[[Bundle, random.Random], Outcome]
    control: Callable[[Bundle, random.Random], None] | None = None  # clean-but-converted control
    consistent: bool = False  # forger recomputed everything the checks use: known miss, not counted
    forensic_only: bool = False  # raster document: content is unreadable offline (needs a model)
    warning_counts: str | None = None  # a stage-warning substring that counts as 'noticed'
    sanity: bool = False  # calibration attack that SHOULD be caught (shows the harness can detect)
    #: on_error=True attacks may legitimately break extraction; the runner records the status
    expect: str = "evade"  # "evade" (we expect a gap) or "detect" (calibration)


REGISTRY: list[Attack] = []


def attack(
    id: str,
    family: str,
    title: str,
    scenario: str = "loan",
    *,
    consistent: bool = False,
    sanity: bool = False,
    forensic_only: bool = False,
    warning_counts: str | None = None,
    control: Callable[[Bundle, random.Random], None] | None = None,
):
    def deco(fn):
        REGISTRY.append(
            Attack(
                id,
                family,
                title,
                scenario,
                fn,
                control=control,
                consistent=consistent,
                sanity=sanity,
                forensic_only=forensic_only,
                warning_counts=warning_counts,
            )
        )
        return fn

    return deco


# ----------------------------------------------------------------------------- base bundles


def build_base(scenario: str, cache: Path, seed: int = 4002) -> Bundle:
    """Generate (once) and load the clean base bundle for ``scenario``."""
    from scripts import make_bundle

    bdir = cache / "bundles" / f"{scenario}-clean"
    if not (bdir / "manifest.json").exists():
        make_bundle.main(
            ["--scenario", scenario, "--variant", "clean", "--seed", str(seed), "--out", str(cache)]
        )
    manifest = json.loads((bdir / "manifest.json").read_text(encoding="utf-8"))
    order = manifest["upload_order"]["digital"]
    files = {Path(rel).name: (bdir / rel).read_bytes() for rel in order}
    reg = manifest.get("registry_path")
    reg_path = (bdir / reg).resolve() if reg else None
    return Bundle(scenario, files, reg_path if reg_path and reg_path.exists() else None, manifest)


# ----------------------------------------------------------------------------- running


@dataclass
class FlagRec:
    rule: str
    severity: str
    files: tuple[str, ...]
    evidence: str


@dataclass
class RunRec:
    flags: list[FlagRec]
    score: float | None
    band: str | None
    lane: str | None
    coverage_ran: int
    coverage_applicable: int
    partial: bool
    status: str
    read: dict[str, str]  # file name -> flat text of the extracted words (for must_read)
    warnings: list[str] = field(default_factory=list)

    def medhigh(self) -> list[FlagRec]:
        return [f for f in self.flags if f.severity in ("medium", "high")]


def analyse(bundle: Bundle, workroot: Path, tag: str) -> RunRec:
    from app.core.config import Settings
    from app.core.db import Database
    from app.core.registry import load_registry
    from scripts.run_case import analyse_files

    d = Path(tempfile.mkdtemp(prefix=f"rt-{tag}-", dir=workroot))
    inp = d / "in"
    inp.mkdir()
    paths = []
    for name, data in bundle.files.items():
        p = inp / name
        p.write_bytes(data)
        paths.append(p)
    settings = Settings(
        _env_file=None,
        data_dir=d / "data",
        db_path=str(d / "data" / "gldb.sqlite3"),
        llm_backend="fake",
        semantic="off",
    )
    db = Database(settings.database_path, data_dir=settings.data_dir)
    db.init_schema()
    registry = load_registry(bundle.registry_path) if bundle.registry_path else None
    case = analyse_files(
        paths,
        use_case=USE_CASE[bundle.scenario],
        today=TODAY,
        db=db,
        settings=settings,
        registry=registry,
    )
    case = db.load_case(case.id)
    names = {f.id: f.name for f in case.files}
    doc_file = {doc.id: names.get(doc.file_id, doc.file_id) for doc in case.documents}
    flags = []
    for fl in case.flags:
        fs = tuple(sorted({doc_file.get(i, i) for i in fl.document_ids}))
        flags.append(FlagRec(fl.rule, fl.severity, fs, fl.evidence))
    read: dict[str, str] = {}
    for doc in case.documents:
        parts = [w.text for w in doc.words]
        for c in doc.fields.values():
            parts += [str(c.raw), str(c.value)]
        for t in doc.tables.values():
            for row in t.rows:
                for c in row.cells.values():
                    parts += [str(c.raw), str(c.value)]
        txt = " ".join(parts).replace(",", "")
        read[doc_file.get(doc.id, doc.id)] = read.get(doc_file.get(doc.id, doc.id), "") + " " + txt
    shutil.rmtree(d, ignore_errors=True)
    cov = case.coverage
    warns = [w for st in case.stages for w in st.warnings]
    warns += [
        f"injection_suspected:{v}"
        for st in case.stages
        for k, v in st.detail.items()
        if k == "injection_suspected" and v
    ]
    return RunRec(
        flags,
        case.score.value if case.score else None,
        case.score.band if case.score else None,
        case.lane,
        cov.ran if cov else 0,
        cov.applicable if cov else 0,
        case.partial,
        case.status,
        read,
        [str(w) for w in warns],
    )


@dataclass
class Result:
    id: str
    family: str
    title: str
    scenario: str
    consistent: bool
    sanity: bool
    forensic_only: bool
    claim: str
    targets: list[str]
    verdict: str  # detected | evaded
    silent: bool
    attributable: list[dict]
    control_medhigh: list[dict]
    all_medhigh: list[dict]
    score: float | None
    band: str | None
    lane: str | None
    control_score: float | None
    control_lane: str | None
    coverage: str
    control_coverage: str
    unread: list[str]
    not_visible: list[str]
    changed: bool
    status: str
    error: str | None = None
    notes: str = ""
    warnings_hit: list[str] = field(default_factory=list)


def _key(f: FlagRec) -> tuple:
    return (f.rule, f.files)


def judge(
    a: Attack, out: Outcome, run: RunRec, control: RunRec, changed: bool, not_visible: list[str]
) -> Result:
    ctrl_keys = {_key(f) for f in control.flags}
    attrib = [f for f in run.flags if _key(f) not in ctrl_keys and f.severity != "info"]
    attrib_mh = [f for f in attrib if f.severity in ("medium", "high")]
    # a control flag whose severity went up counts too
    ctrl_sev = {_key(f): f.severity for f in control.flags}
    rank = {"info": 0, "low": 1, "medium": 2, "high": 3}
    for f in run.flags:
        k = _key(f)
        if (
            k in ctrl_sev
            and rank[f.severity] > rank[ctrl_sev[k]]
            and f.severity in ("medium", "high")
        ):
            attrib_mh.append(f)
    noticed = bool(a.warning_counts) and any(a.warning_counts in w for w in run.warnings)
    verdict = "detected" if (attrib_mh or noticed) else "evaded"
    silent = verdict == "evaded" and not attrib and run.lane == control.lane
    unread = [
        s for s in out.must_read if not any(s.replace(",", "") in t for t in run.read.values())
    ]

    def d(f: FlagRec) -> dict:
        return {
            "rule": f.rule,
            "severity": f.severity,
            "files": list(f.files),
            "evidence": f.evidence[:160],
        }

    return Result(
        a.id,
        a.family,
        a.title,
        a.scenario,
        a.consistent,
        a.sanity,
        a.forensic_only,
        out.claim,
        out.targets,
        verdict,
        silent,
        [d(f) for f in attrib],
        [d(f) for f in control.medhigh()],
        [d(f) for f in run.medhigh()],
        run.score,
        run.band,
        run.lane,
        control.score,
        control.lane,
        f"{run.coverage_ran}/{run.coverage_applicable}",
        f"{control.coverage_ran}/{control.coverage_applicable}",
        unread,
        not_visible,
        changed,
        run.status,
        notes=out.notes,
        warnings_hit=[w for w in run.warnings if a.warning_counts and a.warning_counts in w],
    )


def check_visible(b: Bundle, out: Outcome) -> list[str]:
    """Strings a human should see on the rendered page but OCR cannot find (attack did not land)."""
    import subprocess

    import fitz

    missing = []
    for fname, text in out.must_see:
        doc = fitz.open(stream=b.files[fname], filetype="pdf")
        ok = False
        for page in doc:
            pix = page.get_pixmap(dpi=220)
            with tempfile.TemporaryDirectory() as td:
                png = Path(td) / "p.png"
                pix.save(png)
                r = subprocess.run(
                    ["tesseract", str(png), "stdout", "--psm", "6"], capture_output=True, text=True
                )
            if text.replace(",", "") in r.stdout.replace(",", ""):
                ok = True
                break
        if not ok:
            missing.append(f"{fname}:{text}")
    return missing


class Runner:
    """Runs attacks one at a time, keeping the clean bases and their control runs."""

    def __init__(self, cache: Path, seed: int = SEED):
        self.cache = cache
        self.seed = seed
        self.work = Path(tempfile.mkdtemp(prefix="rt-work-", dir=cache))
        self.bases: dict[str, Bundle] = {}
        self.controls: dict[str, RunRec] = {}

    def close(self) -> None:
        shutil.rmtree(self.work, ignore_errors=True)

    def base(self, scenario: str) -> Bundle:
        if scenario not in self.bases:
            self.bases[scenario] = build_base(scenario, self.cache)
        return self.bases[scenario]

    def control(self, scenario: str) -> RunRec:
        """The unmodified clean base through the same pipeline."""
        if scenario not in self.controls:
            self.controls[scenario] = analyse(self.base(scenario), self.work, f"ctl-{scenario}")
        return self.controls[scenario]

    def run(self, a: Attack) -> Result:
        base, ctrl = self.base(a.scenario), self.control(a.scenario)
        if a.control is not None:  # same conversion, no forgery: what the pipeline says about it
            ck = f"{a.scenario}:{a.control.__name__}"
            if ck not in self.controls:
                cb = base.copy()
                a.control(cb, random.Random(f"{self.seed}:{ck}"))
                self.controls[ck] = analyse(cb, self.work, f"ctl-{a.control.__name__}")
            ctrl = self.controls[ck]
        b = base.copy()
        rng = random.Random(f"{self.seed}:{a.id}")
        try:
            out = a.build(b, rng)
            changed = b.files != base.files
            run = analyse(b, self.work, a.id)
            return judge(a, out, run, ctrl, changed, check_visible(b, out))
        except Exception as exc:  # an attack that crashes is reported, never hidden
            return Result(
                a.id, a.family, a.title, a.scenario, a.consistent, a.sanity, a.forensic_only,
                "", [], "error", False, [], [], [], None, None, None, None, None, "", "", [], [],
                False, "error", error=f"{type(exc).__name__}: {exc}",
            )  # fmt: skip


def run_all(
    attacks: list[Attack],
    cache: Path,
    seed: int = SEED,
    only: set[str] | None = None,
    log: Callable[[str], None] = print,
) -> list[Result]:
    runner = Runner(cache, seed)
    results: list[Result] = []
    try:
        for a in attacks:
            if only and a.id not in only:
                continue
            res = runner.run(a)
            results.append(res)
            log(
                f"{a.id:8s} {res.verdict:9s} silent={res.silent!s:5s} lane={res.lane} "
                f"flags={[(f['rule'], f['severity']) for f in res.attributable]}"
                + (f" ERROR {res.error}" if res.error else "")
                + (f" UNREAD {res.unread}" if res.unread else "")
                + (f" NOTVISIBLE {res.not_visible}" if res.not_visible else "")
            )
    finally:
        runner.close()
    return results
