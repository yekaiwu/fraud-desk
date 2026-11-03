"""Where does each check put its flags? An audit of ``Flag.region`` over real runs.

    uv run python backend/scripts/audit_flag_locations.py --out docs/flag-locations.md [--attacks R01,R02]

Runs every red-team attack and every sample scenario through the pipeline (fake model, digital PDFs) and
classifies each flag by where it points (the same three kinds the page viewer draws):

* ``box``       a region on a page (a figure, a row, a field)
* ``page``      a region that covers the whole page (an unreadable or unclassified page)
* ``document``  no region, but a document: the viewer opens the document's first page and frames it
* ``case``      neither a region nor a document

and lists, per registered check, how many places in the code build its flags, how many of those never pass a
region, and which kinds were seen in the runs (red-team attacks, the sample scenarios, the showcase documents and,
with ``--observed``, every flag the test suite builds: wrap ``Flag.__init__`` in a pytest plugin and dump the kinds). A check that was never seen is listed as such: it needs a hand
test, not a guess. Whatever a check does at run time (a figure with no box on a scanned page, a notice about a
page it could not read), the page viewer handles all four kinds the same way, so the table says what to expect,
it is not a list of exceptions. The output is a Markdown table; nothing here changes a flag.
"""

from __future__ import annotations

import argparse
import collections
import random
import shutil
import sys
import tempfile
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.checks.registry import all_checks  # noqa: E402
from app.core.models import Flag  # noqa: E402

WHOLE_PAGE = 0.9  # a region this large in both directions marks a page, not a spot on it


def static_sites() -> tuple[dict[str, list[bool]], list[str]]:
    """Per rule named in the code: one entry per ``make_flag`` call, True when it passes ``region=``.
    Calls whose rule is a variable (notices, 'reading incomplete', model-derived flags) are listed apart."""
    import ast

    by_rule: dict[str, list[bool]] = collections.defaultdict(list)
    dynamic: list[str] = []
    for path in sorted((_BACKEND / "app").rglob("*.py")):
        tree = ast.parse(path.read_text())
        for n in ast.walk(tree):
            if not (
                isinstance(n, ast.Call)
                and getattr(n.func, "id", getattr(n.func, "attr", None)) == "make_flag"
            ):
                continue
            has = any(
                k.arg == "region"
                and not (isinstance(k.value, ast.Constant) and k.value.value is None)
                for k in n.keywords
            )
            if n.args and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str):
                by_rule[n.args[0].value].append(has)
            else:
                dynamic.append(
                    f"{path.relative_to(_BACKEND)}:{n.lineno} ({'passes a region' if has else 'no region'})"
                )
    return by_rule, dynamic


def kind_of(flag: Flag) -> str:
    r = flag.region
    if r is not None:
        return "page" if r.w >= WHOLE_PAGE and r.h >= WHOLE_PAGE else "box"
    return "document" if flag.document_ids else "case"


def _run(bundle, workroot: Path, tag: str):  # noqa: ANN001, ANN202
    from app.core.config import Settings
    from app.core.db import Database
    from app.core.registry import load_registry
    from scripts.red_team.core import TODAY, USE_CASE
    from scripts.run_case import analyse_files

    d = Path(tempfile.mkdtemp(prefix=f"loc-{tag}-", dir=workroot))
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
    shutil.rmtree(d, ignore_errors=True)
    return case


def collect(only: set[str] | None) -> dict[str, collections.Counter]:
    from scripts.red_team import attacks  # noqa: F401  (registers the attacks)
    from scripts.red_team.core import REGISTRY, Runner

    seen: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    work = Path(tempfile.mkdtemp(prefix="loc-audit-"))
    runner = Runner(work)
    try:
        for scenario in ("loan", "kyc", "aml"):
            for flag in _run(runner.base(scenario), work, f"base-{scenario}").flags:
                seen[flag.rule][kind_of(flag)] += 1
        for a in REGISTRY:
            if only and a.id not in only:
                continue
            base = runner.base(a.scenario)
            b = base.copy()
            try:
                a.build(b, random.Random(f"4002:{a.id}"))
                case = _run(b, work, a.id)
            except Exception as exc:  # an attack that cannot be built is skipped, not hidden
                print(f"{a.id}: skipped ({type(exc).__name__}: {exc})", file=sys.stderr)
                continue
            for flag in case.flags:
                seen[flag.rule][kind_of(flag)] += 1
        from types import SimpleNamespace

        from scripts import make_showcase_bundles as sb

        show = Path(tempfile.mkdtemp(prefix="loc-show-", dir=work))
        for entry in sb.build(show / "out", 1):
            files = {Path(f).name: (show / "out" / f).read_bytes() for f in entry["files"]}
            bundle = SimpleNamespace(files=files, registry_path=None, scenario=entry["use_case"])
            for flag in _run(bundle, work, entry["slug"]).flags:
                seen[flag.rule][kind_of(flag)] += 1
    finally:
        runner.close()
        shutil.rmtree(work, ignore_errors=True)
    return seen


def render(seen: dict[str, collections.Counter]) -> str:
    sites, dynamic = static_sites()
    checks = sorted(all_checks(), key=lambda c: c.rule)
    lines = [
        "| Check | Code sites | Never pass a region | Seen: box | page | document | case | What Show on document does |",
        "|---|---|---|---|---|---|---|---|",
    ]
    never = []
    for c in checks:
        k = seen.get(c.rule, collections.Counter())
        total = sum(k.values())
        st = sites.get(c.rule, [])
        none = sum(1 for has in st if not has)
        if not total:
            never.append(c.rule)
        can = []
        if not st or any(st):
            can.append("draws a box on the figure (when the figure has a position on the page)")
        if none or not st or k["document"]:
            can.append(
                "otherwise opens the document and frames it, saying it is about the file and not one spot"
            )
        if k["case"]:
            can.append(
                "with no document either, says in the caption that it concerns the case as a whole"
            )
        if k["page"]:
            can.append("frames the whole page for an unreadable or unclassified page")
        lines.append(
            f"| `{c.rule}` | {len(st)} | {none} | {k['box']} | {k['page']} | {k['document']} | {k['case']} | {'; '.join(can)} |"
        )
    out = "\n".join(lines)
    out += f"\n\n{len(checks)} registered checks; {len(checks) - len(never)} produced at least one flag in these runs."
    if never:
        out += (
            "\nNot seen in these runs (the code sites above are the evidence for these): "
            + ", ".join(f"`{r}`" for r in never)
            + "."
        )
    out += "\n\nFlags built with a rule name that is a variable (notices about a page a check could not cover, readings that were incomplete, model-derived flags) are:\n"
    out += "\n".join(f"- `{d}`" for d in dynamic) + "\n"
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path)
    ap.add_argument("--attacks", help="comma list of attack ids (default: all)")
    ap.add_argument(
        "--observed",
        type=Path,
        help="JSON {rule: {kind: count}} of flags built during a test run (see the module docstring), merged in",
    )
    a = ap.parse_args(argv)
    only = set(a.attacks.split(",")) if a.attacks else None
    seen = collect(only)
    if a.observed:
        import json

        for rule, kinds in json.loads(a.observed.read_text()).items():
            for kind, n in kinds.items():
                seen[rule][kind] += n
    text = render(seen)
    if a.out:
        a.out.write_text(text)
        print(f"wrote {a.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
