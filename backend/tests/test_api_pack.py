"""Case pack: JSON and HTML (review-workflow tests 28 and 29, section 4.10, 4.13)."""

from __future__ import annotations

import base64
import io
import re

import pytest
from PIL import Image

from app.core.config import Settings
from app.core.db import Database
from app.core.models import Region
from app.pack import (
    CONFIDENTIALITY,
    DISCLAIMER,
    STUDENT_CAVEAT,
    build_pack,
    build_page_images,
    render_pack_html,
)
from tests.test_api_support import (
    Env,
    FakeClock,
    make_env,
    mk_flag,
    review_body,
    tiny_pdf,
    write_fixture,
)

TOP_KEYS = [
    "pack_version",
    "generated_at",
    "disclaimer",
    "caveat",
    "confidentiality",
    "case",
    "applicant",
    "files",
    "documents",
    "not_checked_documents",
    "score",
    "coverage",
    "findings",
    "flags",
    "check_runs",
    "stages",
    "cost_summary",
    "provenance",
    "review",
    "unresolved_at_close",
    "escalation",
    "versions",
    "audit",
]
EVIL = "<script>alert(1)</script>"


@pytest.fixture
def env(settings: Settings, db: Database) -> Env:
    return make_env(settings, db)


def region(page: int = 1) -> Region:
    return Region(file_id="f1", page=page, x=0.1, y=0.2, w=0.4, h=0.1, label="balance")


def test_pack_json_has_exactly_the_documented_keys_and_content(env: Env) -> None:
    high = mk_flag("bs_running_balance", "high", region=region())
    low = mk_flag("bs_dates", "low", doc="d2", locator="transactions:1")
    case = env.seed(
        flags=[high, low], applicant_name="Example Trading", files=[("a.pdf", tiny_pdf())]
    )
    env.review(case.id, **review_body("flag_decision", "confirm", flag_ids=[high.id]))
    r = env.client.get(f"/cases/{case.id}/pack")
    assert r.status_code == 200 and r.headers["content-type"] == "application/json"
    assert r.headers["content-disposition"] == f'attachment; filename="case-{case.id}-v1.json"'
    pack = r.json()
    assert list(pack) == TOP_KEYS
    assert pack["pack_version"] == "1" and pack["disclaimer"] == DISCLAIMER
    assert pack["caveat"] == STUDENT_CAVEAT and pack["confidentiality"] == CONFIDENTIALITY
    assert pack["case"]["id"] == case.id and pack["case"]["version"] == 1
    assert set(pack["case"]) == {
        "id",
        "reference",
        "use_case",
        "status",
        "version",
        "today",
        "created_at",
        "updated_at",
        "decision",
        "lane",
        "top_finding",
    }
    assert pack["applicant"]["name"] == "Example Trading"
    assert (
        pack["files"][0]["sha256"] == case.files[0].sha256
        and pack["files"][0]["mime"] == "application/pdf"
    )
    assert set(pack["flags"][0]) == {
        "id",
        "rule",
        "stage",
        "severity",
        "document_ids",
        "table",
        "field",
        "row",
        "region",
        "extra_regions",
        "evidence",
        "explanation",
        "disposition",
    }
    by_id = {f["id"]: f for f in pack["flags"]}
    assert by_id[high.id]["disposition"] == "confirmed" and by_id[low.id]["disposition"] == "open"
    assert by_id[high.id]["region"]["page"] == 1
    assert pack["score"]["value"] == 35 and pack["coverage"]["ran"] == 1
    assert {f["id"] for f in pack["findings"]} == {"fnd_1", "fnd_2"}
    assert pack["review"][0]["kind"] == "flag_decision" and pack["unresolved_at_close"] == []
    assert pack["escalation"]["escalated_at"] is None
    assert [v["version"] for v in pack["versions"]] == [1]
    assert [a["action"] for a in pack["audit"]] == ["review.flag_decision"]
    assert set(pack["cost_summary"]) >= {"total_usd", "calls"}
    assert "words" not in str(pack["documents"])
    write_fixture("pack", pack)
    # every export is audited
    assert env.audit(case.id)[-1]["action"] == "pack.exported"
    assert env.audit(case.id)[-1]["detail"] == {"format": "json", "version": 1}


def test_pack_for_a_processing_or_unknown_case(env: Env) -> None:
    processing = env.seed(flags=[], status="processing")
    r = env.client.get(f"/cases/{processing.id}/pack")
    assert r.status_code == 409 and r.json()["error"]["code"] == "case_processing"
    assert (
        env.client.get(f"/cases/{processing.id}/pack", params={"format": "html"}).status_code == 409
    )
    assert env.client.get("/cases/c_nope/pack").status_code == 404
    assert (
        env.client.get(f"/cases/{processing.id}/pack", params={"format": "pdf"}).status_code == 422
    )
    assert all(e["action"] != "pack.exported" for e in env.audit(processing.id))


def test_pack_version_reads_a_snapshot(env: Env) -> None:
    case = env.seed(flags=[mk_flag("bs_dates", "low")])
    env.review(case.id, **review_body("flag_decision", "dismiss", flag_ids=[case.flags[0].id]))
    assert env.client.post(f"/cases/{case.id}/rerun").status_code == 202
    env.app.state.runner.wait(env.db.latest_job(case.id).id, timeout=60)  # type: ignore[union-attr]
    old = env.client.get(f"/cases/{case.id}/pack", params={"version": 1}).json()
    assert old["case"]["version"] == 1
    assert old["flags"][0]["disposition"] == "dismissed"
    current = env.client.get(f"/cases/{case.id}/pack").json()
    assert current["case"]["version"] == 2 and current["flags"] == []
    assert [e["case_version"] for e in current["review"]] == [1]  # the full log, tagged by version
    assert env.client.get(f"/cases/{case.id}/pack", params={"version": 9}).status_code == 404


def test_escalation_clock_and_elapsed_time_in_json_and_html(
    settings: Settings, db: Database
) -> None:
    clock = FakeClock()
    env = make_env(settings, db, clock)
    case = env.seed(use_case="aml", flags=[])
    r = env.review(case.id, **review_body("verdict", "escalate_aml", reason="Suspicious routing"))
    stamp = r.json()["entry"]["created_at"]
    clock.advance(hours=5, minutes=30)
    pack = env.client.get(f"/cases/{case.id}/pack").json()
    assert pack["escalation"]["escalated_at"] == stamp
    assert "does not file an STR" in pack["escalation"]["note"]
    html = env.client.get(f"/cases/{case.id}/pack", params={"format": "html"}).text
    assert stamp in html and "5 h 30 min elapsed" in html


# 29. HTML ------------------------------------------------------------------------------------------


def test_html_escapes_untrusted_text_and_is_self_contained(env: Env) -> None:
    flag = mk_flag("bs_running_balance", "high", evidence=EVIL, explanation="Name </td><b>bold</b>")
    case = env.seed(
        flags=[flag], applicant_name="</td><b>", reference="<img src=x onerror=alert(2)>"
    )
    env.review(
        case.id, **review_body("flag_decision", "confirm", flag_ids=[flag.id], reason=EVIL + " ok")
    )
    r = env.client.get(f"/cases/{case.id}/pack", params={"format": "html"})
    assert r.status_code == 200 and r.headers["content-type"] == "text/html; charset=utf-8"
    html = r.text
    assert "<script" not in html.lower()
    assert "&lt;img src=x onerror=alert(2)&gt;" in html  # the reference is shown as text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "&lt;/td&gt;&lt;b&gt;" in html
    assert "<img src=x" not in html and "<b>bold" not in html
    assert not re.search(r"https?://", html)
    assert "<link" not in html and "url(" not in html and "@import" not in html
    assert DISCLAIMER in html and STUDENT_CAVEAT in html and CONFIDENTIALITY in html
    assert html.count(DISCLAIMER) >= 2  # header and footer
    assert "@page { size: A4" in html and "break-inside: avoid" in html
    assert case.id in html and "confirmed" in html
    assert env.audit(case.id)[-1]["detail"]["format"] == "html"


def test_html_shows_partial_scores_not_checked_documents_and_unresolved_dismissals(
    env: Env,
) -> None:
    from app.core.models import CheckRun

    high = mk_flag("bs_running_balance", "high")
    case = env.seed(
        use_case="kyc",
        flags=[high],
        partial=True,
        check_runs=[
            CheckRun(rule="bs_running_balance", stage="rules", status="ran"),
            CheckRun(rule="inv_dates", stage="rules", status="skipped", reason="no invoice"),
        ],
    )
    env.review(case.id, **review_body("flag_decision", "dismiss", flag_ids=[high.id]))
    env.review(case.id, **review_body("verdict", "refer_edd", reviewer="b.lim"))
    html = env.client.get(f"/cases/{case.id}/pack", params={"format": "html"}).text
    assert "partial" in html and "no invoice" in html and "1 of 2 checks ran" in html
    assert "Unresolved at close" in html and "r1" in html
    assert "refer_edd" in html and "pending_second_approval" in html


def build_pdf_with_pages(pages: int) -> bytes:
    return tiny_pdf("Statement", pages=pages)


def test_html_embeds_flagged_pages_as_small_data_uri_pngs_with_boxes(env: Env) -> None:
    flag = mk_flag("bs_running_balance", "high", region=region(1))
    case = env.seed(flags=[flag], files=[("a.pdf", build_pdf_with_pages(2))])
    html = env.client.get(f"/cases/{case.id}/pack", params={"format": "html"}).text
    uris = re.findall(r'src="(data:image/png;base64,[A-Za-z0-9+/=]+)"', html)
    assert len(uris) == 1  # only the page that carries a flag region
    img = Image.open(io.BytesIO(base64.b64decode(uris[0].split(",", 1)[1])))
    assert img.format == "PNG" and img.width <= 800
    # the high flag box is drawn in red around the region
    x = int(0.1 * img.width) + 1
    y = int(0.2 * img.height) + 1
    r, g, b = img.convert("RGB").getpixel((x, y + 20))
    assert r > 150 and g < 80 and b < 80
    assert "Page 1 of file f1" in html and "flags 1" in html


def test_at_most_twenty_page_images_the_rest_are_listed_as_omitted(env: Env) -> None:
    flags = [
        mk_flag("bs_dates", "low", doc="d1", locator=f"transactions:{i}", region=region(i))
        for i in range(1, 23)
    ]
    case = env.seed(flags=flags, files=[("a.pdf", build_pdf_with_pages(22))])
    pack = build_pack(env.db, case.id)
    images = build_page_images(env.db, pack)
    assert len(images) == 20 and all(k[0] == "f1" for k in images)
    html = render_pack_html(pack, page_images=images)
    assert html.count("data:image/png;base64,") == 20
    assert html.count("Image omitted: file f1") == 2
    assert (
        render_pack_html(pack).count("data:image") == 0
    )  # no images given: all omitted, still valid
    assert not re.search(r"https?://", html)


def test_missing_file_or_page_skips_the_image_without_failing(env: Env) -> None:
    flag = mk_flag("bs_running_balance", "high", region=region(7))  # page 7 of a 1-page file
    case = env.seed(flags=[flag], files=[("a.pdf", build_pdf_with_pages(1))])
    r = env.client.get(f"/cases/{case.id}/pack", params={"format": "html"})
    assert r.status_code == 200 and "Image omitted: file f1, page 7" in r.text


def test_wording_check_disclaimer_texts_are_exact() -> None:
    assert DISCLAIMER == "Decision support only; a human decides; not legal advice"
    assert "our reading as students and is not legal advice" in STUDENT_CAVEAT
    assert "[confirm]" in STUDENT_CAVEAT and "tipping-off" in CONFIDENTIALITY
