import hashlib
import random

from app.checks.registry import all_checks, make_flag
from app.core.llm import register_prompt
from app.core.models import Annotation, Classification, FileRef, PageInfo
from app.core.provenance import (
    build_provenance,
    classifier_versions,
    extractor_versions,
    flags_digest,
    ruleset_version,
)
from app.core.testing import FIXED_TODAY, make_ctx, make_doc, make_table


def flags(n: int = 5):  # noqa: ANN201
    return [
        make_flag("x_rule", docs=[f"d{i}"], evidence=f"e{i}", explanation="x", severity="low")
        for i in range(n)
    ]


def test_flags_digest_ignores_order_and_changes_with_content() -> None:
    fl = flags()
    shuffled = list(fl)
    random.Random(1).shuffle(shuffled)
    assert flags_digest(fl) == flags_digest(shuffled)
    changed = [f.model_copy(update={"evidence": "other"}) if f is fl[0] else f for f in fl]
    assert flags_digest(changed) != flags_digest(fl)
    assert flags_digest([]) == hashlib.sha256(b"[]").hexdigest()
    sev = [f.model_copy(update={"severity": "high"}) if f is fl[0] else f for f in fl]
    assert flags_digest(sev) != flags_digest(fl)


def test_ruleset_version_changes_when_a_rule_version_is_bumped() -> None:
    checks = all_checks()
    base = ruleset_version(checks)
    assert base.startswith("rs-") and len(base) == 15
    assert ruleset_version(list(reversed(checks))) == base
    bumped = [
        c.model_copy(update={"version": "2"}) if c.rule == "bs_running_balance" else c
        for c in checks
    ]
    assert ruleset_version(bumped) != base
    expected = (
        "rs-"
        + hashlib.sha256(
            "\n".join(sorted(f"{c.rule}@{c.version}" for c in checks)).encode()
        ).hexdigest()[:12]
    )
    assert base == expected


def test_classifier_and_extractor_versions_are_derived_from_the_data() -> None:
    cls = Classification(label="bank_statement", probability=0.9, classifier="rules", version="1")
    llm = Classification(
        label="bank_statement", probability=0.9, classifier="llm:fake", version="1"
    )
    ann = {"c4": Annotation(label="other", probability=0.5, classifier="rules", version="2")}
    doc = make_doc("bank_statement", id="d1", issuer=cls, extractor="text_layer@1")
    doc.tables["transactions"] = make_table("transactions", [{"credit": "1.00"}], annotations=[ann])
    ctx = make_ctx(
        [doc],
        files_extra=[
            FileRef(
                id="f1",
                name="a",
                sha256="a" * 64,
                size_bytes=1,
                mime="application/pdf",
                pages=[
                    PageInfo(
                        page=1,
                        width_pt=1,
                        height_pt=1,
                        has_text_layer=True,
                        is_scan=False,
                        classification=cls,
                    ),
                    PageInfo(
                        page=2,
                        width_pt=1,
                        height_pt=1,
                        has_text_layer=True,
                        is_scan=False,
                        classification=llm,
                    ),
                ],
            )
        ],
    )
    assert classifier_versions(ctx.case) == {
        "C1": "llm:fake@1+rules@1",
        "C3": "rules@1",
        "C4": "rules@2",
    }
    assert extractor_versions(ctx.case) == {"bank_statement": "text_layer@1"}
    assert classifier_versions(make_ctx().case) == {}


def test_build_provenance_contents() -> None:
    register_prompt("test:prov-a", "alpha")
    register_prompt("test:prov-b", "beta")
    ref = FileRef(id="f1", name="a", sha256="0" * 64, size_bytes=0, mime="application/pdf")
    ctx = make_ctx([make_doc("invoice", id="d1")], files=[(ref, b"%PDF- test")])
    ctx.case.flags = flags(2)
    p = build_provenance(ctx)
    assert p.today == FIXED_TODAY and p.app_version == "0.1.0"
    assert p.ruleset_version == ruleset_version(all_checks())
    assert p.models == {"extract_cheap": "fake", "extract_strong": "fake", "classifier_c1": "rules"}
    assert p.prompt_hashes["test:prov-a"] == hashlib.sha256(b"alpha").hexdigest()
    assert p.prompt_hash == build_provenance(ctx).prompt_hash and len(p.prompt_hash) == 64
    assert p.input_hashes == {"f1": hashlib.sha256(b"%PDF- test").hexdigest()}
    assert p.flags_digest == flags_digest(ctx.case.flags)
    assert p.created_at is not None and len(p.settings_hash) == 64


def test_settings_hash_tracks_result_changing_settings() -> None:
    from decimal import Decimal

    from app.core.config import Settings

    def h(**kw) -> str:  # noqa: ANN003
        s = Settings(_env_file=None, llm_backend="fake", **kw)
        return build_provenance(make_ctx(settings=s)).settings_hash

    base = h()
    assert h() == base
    assert h(money_tolerance=Decimal("0.5")) != base
    assert h(classifier_mode="cascade") != base
    assert h(local_classifier_model="m") != base
    assert h(llm_model_cheap="x") != base
    assert h(log_level="DEBUG") == base  # does not change results
