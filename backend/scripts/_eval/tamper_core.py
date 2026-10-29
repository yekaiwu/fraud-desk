"""Machinery shared by every tamper builder: context, plan, ground-truth lookups, bundle builds
and the finaliser that writes ``tampered/<id>/`` with only the changed files (others inherited)."""

from __future__ import annotations

import hashlib
import importlib
import json
import random
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from scripts._eval.common import (
    load_manifest,
    posix_rel,
    read_json,
    write_json_file,
)
from scripts._eval.tamper_defs import TAMPERS, TamperDef
from scripts._gen.schema import (
    Box,
    BundleSpec,
    Expected,
    Manifest,
    Mutation,
    TamperRecord,
    Target,
)

D = Decimal


class TamperNotApplicable(Exception):
    """The tamper does not apply to this base (wrong scenario, no suitable row, missing render)."""


def q2(v: Decimal) -> Decimal:
    return v.quantize(D("0.01"), rounding=ROUND_HALF_UP)


def s2(v: Decimal) -> str:
    return format(q2(v), "f")


@dataclass
class Ctx:
    td: TamperDef
    base: Manifest
    base_dir: Path
    gt: dict[str, Any]
    labels: dict[str, Any]
    seed: int
    instance: int
    render: str
    renders: list[str]
    rng: random.Random
    seed_key: str

    @property
    def scenario(self) -> str:
        return self.base.use_case

    @property
    def today(self):  # noqa: ANN201
        return self.base.spec.today

    # ---- ground-truth lookups (documents of the clean base) ----
    def docs(self, doc_type: str) -> list[dict[str, Any]]:
        return sorted(
            (d for d in self.gt["documents"] if d["doc_type"] == doc_type), key=lambda d: d["key"]
        )

    def doc(self, key: str) -> dict[str, Any]:
        d = doc_by_key(self.gt, key)
        if d is None:
            raise TamperNotApplicable(f"base has no document {key}")
        return d

    def pick(self, items: list[Any], what: str = "item") -> Any:
        if not items:
            raise TamperNotApplicable(f"no {what} to tamper in this base")
        return self.rng.choice(items)


@dataclass
class Plan:
    edit: str
    mutations: list[Mutation] = field(default_factory=list)
    hook: Callable[[Any], None] | None = None
    file_edit: Callable[[TmpBundle], None] | None = None
    locate: (
        Callable[
            [dict[str, Any], dict[str, Any]],
            tuple[Target, dict[str, str | None], dict[str, str | None]],
        ]
        | None
    ) = None
    base_override: Path | None = None  # bundle-channel tamper whose base case differs (T16c)


# ------------------------------------------------------------------------------- GT helpers


def doc_by_key(gt: dict[str, Any], key: str) -> dict[str, Any] | None:
    return next((d for d in gt["documents"] if d["key"] == key), None)


def tx_rows(doc: dict[str, Any]) -> list[dict[str, Any]]:
    return doc["tables"]["transactions"]["rows"]


def cell(row: dict[str, Any], col: str) -> str | None:
    c = row["cells"].get(col)
    return None if c is None else c.get("value")


def credit_rows(doc: dict[str, Any]) -> list[dict[str, Any]]:
    return [r for r in tx_rows(doc) if cell(r, "credit") and D(cell(r, "credit")) > 0]


def debit_rows(doc: dict[str, Any]) -> list[dict[str, Any]]:
    return [r for r in tx_rows(doc) if cell(r, "debit") and D(cell(r, "debit")) > 0]


def to_box(b: dict[str, Any] | None) -> Box | None:
    if not b:
        return None
    return Box(file=b["file"], page=b["page"], x=b["x"], y=b["y"], w=b["w"], h=b["h"])


def row_target(gt: dict[str, Any], doc_key: str, row: int, table: str = "transactions") -> Target:
    d = doc_by_key(gt, doc_key)
    box = None
    if d is not None:
        rows = d["tables"][table]["rows"]
        r = next((x for x in rows if x["index"] == row), None)
        if r is not None:
            box = to_box(r.get("box"))
    return Target(doc_key=doc_key, table=table, row=row, box=box)


def field_target(gt: dict[str, Any], doc_key: str, field_name: str) -> Target:
    d = doc_by_key(gt, doc_key)
    box = to_box(d["fields"].get(field_name, {}).get("box")) if d else None
    return Target(doc_key=doc_key, field=field_name, box=box)


def field_value(gt: dict[str, Any], doc_key: str, name: str) -> str | None:
    d = doc_by_key(gt, doc_key)
    if d is None:
        return None
    f = d["fields"].get(name)
    return None if f is None else f.get("value")


def table_cell(gt: dict[str, Any], doc_key: str, table: str, row: int, col: str) -> str | None:
    d = doc_by_key(gt, doc_key)
    if d is None:
        return None
    r = next((x for x in d["tables"][table]["rows"] if x["index"] == row), None)
    return None if r is None else cell(r, col)


def fs_row_index(doc: dict[str, Any], table: str, line: str) -> int:
    for r in doc["tables"][table]["rows"]:
        if cell(r, "line") == line:
            return r["index"]
    raise TamperNotApplicable(f"no {table}.{line}")


def fs_value(doc: dict[str, Any], table: str, line: str, col: str = "current") -> Decimal:
    idx = fs_row_index(doc, table, line)
    r = next(x for x in doc["tables"][table]["rows"] if x["index"] == idx)
    v = cell(r, col)
    return D(v) if v is not None else D(0)


# --------------------------------------------------------------------------- tmp bundle


class TmpBundle:
    """A fully materialised copy of the tampered bundle (every file present) that builders may
    edit in place before ``finalise`` keeps only what differs from the base."""

    def __init__(self, root: Path):
        self.root = root
        self.manifest = load_manifest(root / "manifest.json")
        self.labels = read_json(root / "labels.json")
        self.gt = read_json(root / "ground_truth.json")

    def path(self, rel: str) -> Path:
        return self.root / rel

    def file_entry(self, rel: str) -> Any:
        return next(f for f in self.manifest.files if f.path == rel)

    def replace_file(self, rel: str, data: bytes) -> str:
        """Overwrite ``rel`` with ``data``; update size, sha, page shas, labels. Returns the new sha."""
        fe = self.file_entry(rel)
        old = fe.sha256
        new = hashlib.sha256(data).hexdigest()
        (self.root / rel).write_bytes(data)
        fe.sha256 = new
        fe.size_bytes = len(data)
        for p in fe.pages:
            p.page_sha256 = hashlib.sha256(f"{new}:{p.page}".encode()).hexdigest()
        for row in self.labels.get("pages", []):
            if row.get("sha256") == old:
                row["sha256"] = new
        return new

    def add_file(self, rel: str, data: bytes, like: str, *, upload: bool = True) -> None:
        """Add a second copy ``rel`` of the file ``like`` (T16a, T16b)."""
        src = self.file_entry(like)
        (self.root / rel).parent.mkdir(parents=True, exist_ok=True)
        (self.root / rel).write_bytes(data)
        sha = hashlib.sha256(data).hexdigest()
        entry = src.model_copy(deep=True)
        entry.path = rel
        entry.sha256 = sha
        entry.size_bytes = len(data)
        entry.inherited = False
        for p in entry.pages:
            p.page_sha256 = hashlib.sha256(f"{sha}:{p.page}".encode()).hexdigest()
        self.manifest.files.append(entry)
        if upload:
            self.manifest.upload_order[src.render].append(rel)

    def save(self) -> None:
        write_json_file(self.root / "manifest.json", self.manifest.model_dump(mode="json"))
        write_json_file(self.root / "labels.json", self.labels)
        write_json_file(self.root / "ground_truth.json", self.gt)


# ------------------------------------------------------------------------- world builds


def build_world_bundle(
    spec: BundleSpec, out_dir: Path, hook: Callable[[Any], None] | None = None
) -> Manifest:
    """Build ``spec`` (clean variant plus mutations) into ``out_dir``; ``hook(world)`` may edit the
    world in code after the mutations are applied (edits the recorded mutation ops cannot express)."""
    from scripts import make_bundle

    if hook is None:
        return make_bundle.build_bundle(spec, out_dir)
    mod = importlib.import_module(f"scripts._gen.bundle_{spec.scenario}")
    spec = mod.resolve_spec(spec)
    muts = list(spec.mutations)
    if spec.scenario == "loan":
        from scripts._gen import quirks as qk
        from scripts._gen.world import apply_plot, world_from_spec

        world = apply_plot(world_from_spec(spec), muts)
        hook(world)
        world.variant = spec.variant
        docs = mod.render_docs(world, spec)
        quirk_ids = [q for q in spec.quirks if not (q == "Q09" and world.layout != "ocbc_style")]
        quirks = qk.records(quirk_ids)
    else:
        story, _facts = mod.story_world(spec)
        world = mod.apply_mutations(story, muts)
        hook(world)
        world.variant = spec.variant
        docs = mod.render_docs(world)
        quirks = mod.quirk_records(spec)
    return mod.write_bundle(
        spec,
        out_dir,
        docs,
        applicant={"name": world.company.party.name, "uen": world.company.party.uen},
        expected=Expected(),
        planted=[],
        quirks=quirks,
        doc_date=spec.today,
    )


# ---------------------------------------------------------------------------- finaliser


def finalise(
    tmp: Path,
    ctx: Ctx,
    plan: Plan,
    out_dir: Path,
    bundle_id: str,
    target: Target,
    original: dict[str, str | None],
    edited: dict[str, str | None],
) -> Manifest:
    """Write ``out_dir`` with only the files that differ from the base; inherited files are listed
    with a relative path that may traverse into the base bundle."""
    man = load_manifest(tmp / "manifest.json")
    base_sha = {f.path: f.sha256 for f in ctx.base.files}
    rewrite: dict[str, str] = {}
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for f in man.files:
        if base_sha.get(f.path) == f.sha256 and (ctx.base_dir / f.path).exists():
            newp = posix_rel(ctx.base_dir / f.path, out_dir)
            rewrite[f.path] = newp
            f.path = newp
            f.inherited = True
        else:
            dest = out_dir / f.path
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(tmp / f.path, dest)
    for r, paths in list(man.upload_order.items()):
        man.upload_order[r] = [rewrite.get(p, p) for p in paths]  # type: ignore[index]
    for d in man.documents:
        d.digital_path = rewrite.get(d.digital_path, d.digital_path)
    if target.box is not None:
        target = target.model_copy(
            update={
                "box": target.box.model_copy(
                    update={"file": rewrite.get(target.box.file, target.box.file)}
                )
            }
        )
    man.bundle_id = bundle_id
    man.kind = "tampered"
    man.base_bundle = posix_rel(ctx.base_dir / "manifest.json", out_dir)
    reg = (ctx.base_dir / ctx.base.registry_path).resolve()
    man.registry_path = posix_rel(reg, out_dir)
    man.planted = []
    man.expected = Expected()
    man.tampers = [
        TamperRecord(
            tamper_id=ctx.td.id,
            instance=ctx.instance,
            channel=ctx.td.channel,
            edit=plan.edit,
            target=target,
            original=original,
            edited=edited,
            expected_any_of=list(ctx.td.expected_any_of),
            may_also_fire=list(ctx.td.may_also_fire),
            hard=ctx.td.hard,
            known_miss=ctx.td.known_miss,
            weak=ctx.td.weak,
            seed_key=ctx.seed_key,
        )
    ]
    write_json_file(out_dir / "manifest.json", man.model_dump(mode="json"))
    for name in ("labels.json", "ground_truth.json"):
        shutil.copy2(tmp / name, out_dir / name)
    return man


def make_ctx(
    base: Manifest,
    base_dir: Path,
    tamper_id: str,
    *,
    seed: int,
    instance: int,
    render: str,
    renders: list[str] | None = None,
) -> Ctx:
    td = TAMPERS[tamper_id]
    if base.use_case not in td.scenarios:
        raise TamperNotApplicable(f"{tamper_id} does not apply to scenario {base.use_case}")
    key = f"{seed}:{tamper_id}:{instance}"
    return Ctx(
        td=td,
        base=base,
        base_dir=base_dir,
        gt=read_json(base_dir / "ground_truth.json"),
        labels=read_json(base_dir / "labels.json"),
        seed=seed,
        instance=instance,
        render=render,
        renders=list(renders or [render]),
        rng=random.Random(key),
        seed_key=key,
    )


def dumps_compact(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True)
