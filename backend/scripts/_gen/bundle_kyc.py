"""KYC demo bundle (plan 0.5 KYC row, docs 4.2): kyc-clean, kyc-fraud, kyc-forger.

Sembrook Freight Pte. Ltd. declares logistics. The fraud variant adds crypto counterparties to the
statements, a fourth profile director (Koh Boon Kiat, with an NRIC card) who is not a registry
officer, and a chart that omits Kestrel Capital Holdings (45 + 30 = 75%). The careful forger prints
Tan 60 / Hafiz 40 = 100% on both chart and register, so only the registry comparison remains.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from . import kyc_plots as plots
from .bundle_common import write_bundle
from .kyc_cert import render_certificate
from .kyc_chart import render_chart
from .kyc_common import (
    BS_RULES,
    quirk_records,
    resolve_spec,
    table_box,
    truth_box,
)
from .kyc_passport import render_passport
from .kyc_register import render_register
from .kyc_view import apply_mutations, view_of
from .render_common import RenderedDoc
from .render_id import render_id
from .render_profile import render_profile
from .render_statement import render_statement
from .schema import (
    BundleSpec,
    Expected,
    Manifest,
    Planted,
    RuleExpectation,
    Target,
)
from .world import World, world_from_spec

NAME = "kyc"
D = Decimal
MUST_NOT_FIRE_FORGER = [
    "kyc_shareholdings",
    "id_format",
    "id_checksum",
    "passport_mrz",
    "date_valid",
    "pdf_editor_software",
    "pdf_modified",
    *BS_RULES,
]
__all__ = ["NAME", "build", "render_docs", "resolve_spec", "story_world"]


def story_world(spec: BundleSpec) -> tuple[World, dict[str, str]]:
    """The clean world of the spec's company with the variant's plot applied (no tampers).
    Returns the world and the plot facts the planted records quote."""
    w = world_from_spec(spec)
    view_of(w)  # attach the honest chart / register
    facts: dict[str, str] = {}
    if spec.variant == "clean":
        return w, facts
    w = plots.plant_crypto(w)
    w = plots.plant_director(w)
    w, own = plots.plant_ownership(w, spec.variant)
    facts.update(own)
    return w, facts


def render_docs(world: World) -> list[RenderedDoc]:
    ov = world.producer_overrides
    docs: list[RenderedDoc] = []

    def keep(key: str) -> bool:
        return key not in world.dropped_docs

    docs.append(render_profile(world, ov.get("company_profile:-")))
    for fn, key in (
        (render_certificate, "certificate_of_incorporation:-"),
        (render_chart, "ownership_chart:-"),
        (render_register, "shareholder_register:-"),
    ):
        if keep(key):
            docs.append(fn(world, ov.get(key)))
    on_profile = [p for p in world.people if p.is_director and p.on_profile]
    for n, p in enumerate((p for p in on_profile if p.id_kind == "nric"), start=1):
        if keep(f"national_id:{n}"):
            docs.append(render_id(p, n, ov.get(f"national_id:{n}")))
    for n, p in enumerate((p for p in on_profile if p.id_kind == "passport"), start=1):
        if keep(f"passport:{n}"):
            docs.append(render_passport(world, p, n, ov.get(f"passport:{n}")))
    for st in world.statements:
        if keep(st.key):
            docs.append(render_statement(st, world.quirks, ov.get(st.key)))
    return docs


def _expected(variant: str) -> Expected:
    if variant == "clean":
        return Expected(
            lane_any_of=["fast", "edd"],
            band_max="low",
            verdict="approve",
            must_fire=[],
            max_flags_ge_medium=0,
        )
    fire = [
        RuleExpectation(rule="x_declared_vs_activity", min_severity="medium"),
        RuleExpectation(rule="x_ownership_vs_registry", min_severity="high"),
        RuleExpectation(rule="x_directors", min_severity="high"),
    ]
    if variant == "fraud":
        fire.append(RuleExpectation(rule="kyc_shareholdings"))
        return Expected(
            lane="edd", band_min="high", verdict="refer_edd", must_fire=fire, must_not_fire=[]
        )
    return Expected(
        lane="edd",
        band_min="medium",
        verdict="refer_edd",
        must_fire=fire,
        must_not_fire=[*MUST_NOT_FIRE_FORGER],
    )


def _planted(
    world: World, docs: dict[str, RenderedDoc], facts: dict[str, str], variant: str
) -> list[Planted]:
    if variant == "clean":
        return []
    out: list[Planted] = []
    # P1: crypto counterparties, one record per statement that carries inserted rows
    for k, st in enumerate(world.statements):
        idx = [i for i, r in enumerate(st.rows) if r.kind.startswith("crypto")]
        if not idx:
            continue
        rows = [st.rows[i] for i in idx]
        value = sum((r.debit or r.credit or D(0) for r in rows), D(0))
        out.append(
            Planted(
                id=f"P1{'abcdefgh'[k]}",
                kind="crypto_activity",
                description=(
                    f"{len(rows)} rows with crypto/virtual-asset counterparties totalling {value:.2f} "
                    f"in {st.month}; the profile declares freight and logistics"
                ),
                deck_slide=14,
                target=Target(
                    doc_key=st.key,
                    table="transactions",
                    row=idx[0],
                    box=truth_box(docs, st.key, table="transactions", row=idx[0]),
                ),
                facts={
                    "statement": st.month,
                    "rows": str(len(rows)),
                    "value": f"{value:.2f}",
                    "counterparties": ";".join(sorted({r.counterparty or "" for r in rows})),
                },
            )
        )
    # P2: the ownership chart (and, for the forger, the register) disagrees with the registry
    own_docs = ["ownership_chart:-"] + (["shareholder_register:-"] if variant == "forger" else [])
    for j, key in enumerate(own_docs):
        table = "holders" if key.startswith("ownership") else "shareholders"
        out.append(
            Planted(
                id=f"P2{'ab'[j]}",
                kind="ownership_omitted" if variant == "fraud" else "ownership_redistributed",
                description=(
                    f"{facts['omitted_holder']} ({facts['omitted_percent']}% in the registry) is "
                    f"missing; printed direct holdings total {facts['printed_total']}%"
                ),
                deck_slide=14,
                target=Target(doc_key=key, table=table, box=table_box(docs, key, table)),
                facts=dict(facts),
            )
        )
    # P3: the fourth director and his ID card
    prof = [p for p in world.people if p.on_profile and p.is_director]
    koh = next(p for p in prof if not p.in_registry)
    row = [p.name for p in prof].index(koh.name)
    out.append(
        Planted(
            id="P3a",
            kind="director_not_in_registry",
            description=f"{koh.name} is listed as a director on the profile but is not a registry officer",
            deck_slide=14,
            target=Target(
                doc_key="company_profile:-",
                table="directors",
                row=row,
                box=truth_box(docs, "company_profile:-", table="directors", row=row),
            ),
            facts={"director": koh.name, "position": koh.position},
        )
    )
    nric = [p for p in prof if p.id_kind == "nric"]
    n = [p.name for p in nric].index(koh.name) + 1
    out.append(
        Planted(
            id="P3b",
            kind="director_id_document",
            description=f"NRIC card supplied for {koh.name}, who is not a registry officer",
            deck_slide=14,
            target=Target(
                doc_key=f"national_id:{n}",
                field="id_number",
                box=truth_box(docs, f"national_id:{n}", field="id_number"),
            ),
            facts={"director": koh.name},
        )
    )
    return out


def build(spec: BundleSpec, out_dir: Path) -> Manifest:
    spec = resolve_spec(spec)
    story, facts = story_world(spec)
    world = apply_mutations(story, list(spec.mutations))
    world.variant = spec.variant
    docs = render_docs(world)
    by_key = {d.key: d for d in docs}
    return write_bundle(
        spec,
        out_dir,
        docs,
        applicant={"name": world.company.party.name, "uen": world.company.party.uen},
        expected=_expected(spec.variant),
        planted=_planted(world, by_key, facts, spec.variant),
        quirks=quirk_records(spec),
        doc_date=spec.today,
    )
