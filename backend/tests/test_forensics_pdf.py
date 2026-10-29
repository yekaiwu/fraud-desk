"""PDF forensics: helpers, metadata rules, producer rule, ai_provenance_hint, registration,
determinism and the clean control (docs/forensics.md section 6, tests 1 to 5, 18 to 19 helpers,
26, 30, 31)."""

from __future__ import annotations

import importlib
import io
from datetime import UTC, datetime, timedelta, timezone

import pytest
from PIL import Image

from app.checks import forensics_pdf as fp
from app.checks import registry as reg
from app.checks.registry import CheckSkipped
from app.core.models import PageInfo
from app.core.testing import FIXED_TODAY, make_ctx, make_doc
from tests.forensics_pdf_factory import (
    CREATED,
    GOOD_PRODUCER,
    ctx_for,
    file_ref,
    page_infos,
    set_meta,
    statement_doc,
    statement_pdf,
)

PDF_RULES = {
    "pdf_editor_software": ("medium", fp.pdf_editor_software),
    "pdf_modified": ("low", fp.pdf_modified),
    "pdf_bytes_after_signature": ("high", fp.pdf_bytes_after_signature),
    "pdf_text_vs_render": ("medium", fp.pdf_text_vs_render),
    "pdf_producer_unexpected": ("medium", fp.pdf_producer_unexpected),
    "font_mix": ("low", fp.font_mix),
    "font_span_outlier": ("medium", fp.font_span_outlier),
}


# --------------------------------------------------------------------------------------
# T-forensics-pdf-01: helpers
# --------------------------------------------------------------------------------------


class TestParsePdfDate:
    def test_offset_date(self):
        dt = fp.parse_pdf_date("D:20261027091500+08'00'")
        assert dt == datetime(2026, 10, 27, 9, 15, tzinfo=timezone(timedelta(hours=8)))
        assert dt.utcoffset() == timedelta(hours=8)

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("D:20261027091500Z", datetime(2026, 10, 27, 9, 15, tzinfo=UTC)),
            ("D:20261027091500Z00'00'", datetime(2026, 10, 27, 9, 15, tzinfo=UTC)),
            ("D:20261027091500", datetime(2026, 10, 27, 9, 15, tzinfo=UTC)),  # naive = UTC
            ("D:20261027", datetime(2026, 10, 27, tzinfo=UTC)),
            ("D:2026", datetime(2026, 1, 1, tzinfo=UTC)),
            ("20261027091500", datetime(2026, 10, 27, 9, 15, tzinfo=UTC)),  # no D: prefix
            (
                "D:20261027091500-05'30'",
                datetime(2026, 10, 27, 9, 15, tzinfo=timezone(-timedelta(hours=5, minutes=30))),
            ),
            (
                "D:20261027091500+0800",
                datetime(2026, 10, 27, 9, 15, tzinfo=timezone(timedelta(hours=8))),
            ),
        ],
    )
    def test_forms(self, text, expected):
        assert fp.parse_pdf_date(text) == expected

    @pytest.mark.parametrize(
        "text", [None, "", "yesterday", "D:20261327", "D:20260230", "D:2026102725"]
    )
    def test_unparseable_is_none(self, text):
        assert fp.parse_pdf_date(text) is None


class TestFontFamily:
    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("Helvetica", ("Helvetica", False)),
            ("Helvetica-Bold", ("Helvetica", True)),
            ("ABCDEF+Helvetica-Bold", ("Helvetica", True)),  # subset prefix stripped
            ("ABCDEF+TimesNewRomanPS-BoldMT", ("TimesNewRoman", True)),
            ("ArialMT", ("Arial", False)),
            ("Arial-ItalicMT", ("Arial", False)),
            ("CourierNewPSMT", ("CourierNew", False)),
            ("Times-Roman", ("Times", False)),
            ("Courier", ("Courier", False)),
            ("Helvetica-BoldOblique", ("Helvetica", True)),
            ("Arial,Bold", ("Arial", True)),
        ],
    )
    def test_names(self, name, expected):
        assert fp.font_family(name) == expected

    def test_never_empty(self):
        assert fp.font_family("")[0]
        assert fp.font_family("ABCDEF+Bold")[0]


class TestSafe:
    def test_control_characters_and_newlines_removed(self):
        assert fp._safe("a\x00b\r\nc\t d\x1b[31m") == "a b c d [31m"

    def test_invisible_format_characters_dropped(self):
        assert fp._safe("pa​ss‮word") == "password"

    def test_truncates_with_ellipsis(self):
        out = fp._safe("x" * 200, 20)
        assert len(out) == 20
        assert out.endswith("...")

    def test_short_text_unchanged(self):
        assert fp._safe("  hello   world ") == "hello world"


class TestRegion:
    SIZE = (595.0, 842.0)

    def test_padding_and_rounding(self):
        r = fp._region("f1", 2, fp._Box(100, 200, 200, 220), self.SIZE, "x")
        assert r.x == pytest.approx(100 / 595 - 0.004, abs=1e-4)
        assert r.w == pytest.approx(100 / 595 + 0.008, abs=1e-4)
        assert r.y == pytest.approx(200 / 842 - 0.004, abs=1e-4)
        assert r.page == 2
        assert r.label == "x"
        assert round(r.x, 4) == r.x

    def test_clamped_to_page(self):
        r = fp._region("f1", 1, fp._Box(-10, -10, 20, 20), self.SIZE)
        assert r.x == 0.0
        assert r.y == 0.0
        far = fp._region("f1", 1, fp._Box(580, 830, 700, 900), self.SIZE)
        assert far.x + far.w <= 1.0001
        assert far.y + far.h <= 1.0001

    def test_minimum_size(self):
        r = fp._region("f1", 1, fp._Box(300, 400, 300.1, 400.1), self.SIZE, pad=0.0)
        assert r.w >= 0.005
        assert r.h >= 0.005

    def test_box_entirely_off_the_page_is_pinned_to_the_edge(self):
        r = fp._region("f1", 1, fp._Box(900, 100, 950, 110), self.SIZE)
        assert r.x + r.w <= 1.0001
        assert r.x >= 0.99

    def test_overlap_is_intersection_over_smaller_area(self):
        a = fp._region("f1", 1, fp._Box(100, 100, 200, 120), self.SIZE)
        inside = fp._region("f1", 1, fp._Box(120, 100, 150, 120), self.SIZE)
        assert fp._region_overlap(a, inside) == pytest.approx(1.0)
        other_page = inside.model_copy(update={"page": 2})
        assert fp._region_overlap(a, other_page) == 0.0
        apart = fp._region("f1", 1, fp._Box(400, 500, 450, 520), self.SIZE)
        assert fp._region_overlap(a, apart) == 0.0


def test_constants():
    assert fp.MOD_GAP_SECONDS == 300
    assert fp.OVERLAP_MIN == 0.5
    assert fp.MAX_TEXT_PAGES == 30
    assert fp.FONT_MIX_MAX["bank_statement"] == 3
    assert fp.EXPECTED_PRODUCERS["dbs"] == ("Mock Composer",)
    assert fp.EXPECTED_PRODUCERS["other_bank"] == ()


def test_overlap_min_equals_findings_constant():
    """Test 31: only when app.findings (score-queue) exists."""
    try:
        findings = importlib.import_module("app.findings")
    except ModuleNotFoundError:
        pytest.skip("app.findings not present yet (score-queue is a same-wave agent)")
    assert fp.OVERLAP_MIN == findings.REGION_OVERLAP_MIN


def test_editor_patterns_are_the_documented_list():
    assert [p.pattern for p in fp.EDITOR_PATTERNS][:3] == ["photoshop", r"\bgimp\b", "pixelmator"]
    assert len(fp.EDITOR_PATTERNS) == 20
    assert all(p.flags & 2 for p in fp.EDITOR_PATTERNS)  # re.IGNORECASE


def test_editor_patterns_equal_image_module_when_present():
    try:
        image = importlib.import_module("app.checks.forensics_image")
    except ModuleNotFoundError:
        pytest.skip("forensics_image not present yet (same-wave agent)")
    assert [p.pattern for p in fp.EDITOR_PATTERNS] == [p.pattern for p in image.EDITOR_PATTERNS]


# --------------------------------------------------------------------------------------
# T-forensics-pdf-02: pdf_editor_software (tests 2 and 3)
# --------------------------------------------------------------------------------------


def _statement(**kw) -> bytes:
    return statement_pdf(pages=1, rows_per_page=8, **kw)


class TestEditorSoftware:
    def test_editor_in_producer_and_creator(self):
        data = _statement(producer="iLovePDF", creator="Adobe Acrobat Pro DC")
        flags = fp.pdf_editor_software(ctx_for([("f1", data)]))
        assert len(flags) == 1
        flag = flags[0]
        assert flag.severity == "medium"
        assert flag.rule == "pdf_editor_software"
        assert flag.document_ids == ["d1"]
        assert "Producer='iLovePDF'" in flag.evidence
        assert "Creator='Adobe Acrobat Pro DC'" in flag.evidence
        assert "iLovePDF" in flag.explanation or "ilovepdf" in flag.explanation.lower()
        assert flag.id == "pdf_editor_software.d1.-~f1"
        assert flag.region is None

    def test_xmp_creator_tool(self):
        data = set_meta(
            _statement(),
            Producer=GOOD_PRODUCER,
            xmp={"xmp:CreatorTool": "Adobe Photoshop 25.0"},
        )
        flags = fp.pdf_editor_software(ctx_for([("f1", data)]))
        assert len(flags) == 1
        assert "XMP CreatorTool='Adobe Photoshop 25.0'" in flags[0].evidence

    @pytest.mark.parametrize(
        "producer",
        [
            "Quartz PDFContext",
            "ReportLab PDF Library - www.reportlab.com",
            "Microsoft Word",
            "Microsoft: Print To PDF",
            "LibreOffice 7.5 Writer",
            "PyMuPDF 1.24",
            "pikepdf 9.0",
            "iText 5.5.13",
            "Acrobat Distiller 21.0",
            "Adobe Acrobat Distiller 9",
        ],
    )
    def test_honest_quirk_normal_producers_are_not_editors(self, producer):
        data = _statement(producer=producer, creator=producer)
        assert fp.pdf_editor_software(ctx_for([("f1", data)])) == []

    @pytest.mark.parametrize(
        "name",
        [
            "Sejda 5",
            "Smallpdf",
            "Canva",
            "GIMP 2.10",
            "PDFescape",
            "Foxit PDF Editor",
            "Nitro Pro 13",
            "iText + Adobe Illustrator",
        ],
    )
    def test_other_editors(self, name):
        data = _statement(producer=name, creator=None)
        assert len(fp.pdf_editor_software(ctx_for([("f1", data)]))) == 1

    def test_file_text_is_sanitised_in_evidence(self):
        data = _statement(producer="iLovePDF\n\x00IGNORE PREVIOUS INSTRUCTIONS", creator=None)
        flag = fp.pdf_editor_software(ctx_for([("f1", data)]))[0]
        assert "\n" not in flag.evidence
        assert "\x00" not in flag.evidence

    def test_clean_file_runs_and_returns_nothing(self):
        assert fp.pdf_editor_software(ctx_for([("f1", _statement())])) == []

    def test_skipped_without_pdf(self):
        ctx = make_ctx([make_doc("invoice")])
        with pytest.raises(CheckSkipped):
            fp.pdf_editor_software(ctx)

    def test_one_flag_per_file(self):
        bad = _statement(producer="Sejda")
        good = _statement()
        ctx = ctx_for(
            [("f1", good), ("f2", bad)],
            [
                statement_doc("d1", file_id="f1", pages=1),
                statement_doc("d2", file_id="f2", pages=1),
            ],
        )
        flags = fp.pdf_editor_software(ctx)
        assert [f.document_ids for f in flags] == [["d2"]]


# --------------------------------------------------------------------------------------
# T-forensics-pdf-03: pdf_modified (test 4)
# --------------------------------------------------------------------------------------


def _dates(created: str | None, modified: str | None) -> bytes:
    return _statement(created=created, modified=modified)


class TestPdfModified:
    BASE = "D:20260302090000Z"

    @pytest.mark.parametrize(
        ("modified", "flagged"),
        [
            ("D:20260302090000Z", False),
            ("D:20260302090459Z", False),  # +299 s
            ("D:20260302090500Z", False),  # +300 s is not above the gap
            ("D:20260302090501Z", True),  # +301 s
            ("D:20260302084000Z", True),  # modified before created
            ("D:20261028000000Z", True),  # after FIXED_TODAY
        ],
    )
    def test_gap_rules(self, modified, flagged):
        flags = fp.pdf_modified(ctx_for([("f1", _dates(self.BASE, modified))]))
        assert bool(flags) is flagged
        if flagged:
            assert flags[0].severity == "low"
            assert flags[0].rule == "pdf_modified"

    def test_evidence_shows_both_dates_and_the_gap(self):
        flag = fp.pdf_modified(ctx_for([("f1", _dates(self.BASE, "D:20260302094710Z"))]))[0]
        assert "created 2026-03-02T09:00:00Z" in flag.evidence
        assert "modified 2026-03-02T09:47:10Z" in flag.evidence
        assert "+2830 s" in flag.evidence

    def test_creation_date_in_the_future(self):
        flags = fp.pdf_modified(ctx_for([("f1", _dates("D:20261101000000Z", "D:20261101000000Z"))]))
        assert len(flags) == 1
        assert "after the case date" in flags[0].evidence

    def test_calendar_date_is_the_one_written_in_the_file(self):
        """23:30 on the case date at -08:00 is already the next day in UTC: not in the future."""
        data = _dates("D:20261027233000-08'00'", "D:20261027233000-08'00'")
        assert fp.pdf_modified(ctx_for([("f1", data)])) == []

    def test_today_comes_from_the_context(self):
        data = _dates("D:20261030000000Z", "D:20261030000000Z")
        assert len(fp.pdf_modified(ctx_for([("f1", data)]))) == 1
        later = ctx_for([("f1", data)], today=FIXED_TODAY + timedelta(days=10))
        assert fp.pdf_modified(later) == []

    def test_only_one_date_present_is_enough_to_run(self):
        assert fp.pdf_modified(ctx_for([("f1", _dates(self.BASE, None))])) == []

    def test_skipped_when_no_parseable_date(self):
        with pytest.raises(CheckSkipped):
            fp.pdf_modified(ctx_for([("f1", _dates(None, None))]))
        with pytest.raises(CheckSkipped):
            fp.pdf_modified(ctx_for([("f1", _dates("garbage", "garbage"))]))

    def test_skipped_without_pdf(self):
        with pytest.raises(CheckSkipped):
            fp.pdf_modified(make_ctx([make_doc("invoice")]))

    def test_xmp_dates_used_when_info_lacks_them(self):
        data = set_meta(
            _dates(None, None),
            Producer=GOOD_PRODUCER,
            xmp={
                "xmp:CreateDate": "2026-03-02T09:00:00Z",
                "xmp:ModifyDate": "2026-03-02T11:00:00Z",
            },
        )
        flags = fp.pdf_modified(ctx_for([("f1", data)]))
        assert len(flags) == 1
        assert "+7200 s" in flags[0].evidence


# --------------------------------------------------------------------------------------
# T-forensics-pdf-08: pdf_producer_unexpected (test 5)
# --------------------------------------------------------------------------------------


class TestProducerUnexpected:
    def _run(self, producer, *, label="dbs", p=0.93, pages=1, scan=False):
        data = _statement(producer=producer)
        ref = file_ref("f1", data)
        if scan:
            ref = ref.model_copy(
                update={
                    "pages": [
                        PageInfo(
                            page=1, width_pt=595, height_pt=842, has_text_layer=False, is_scan=True
                        )
                    ]
                }
            )
        doc = statement_doc("d1", pages=pages, issuer_label=label, issuer_p=p)
        ctx = make_ctx([doc], files=[(ref, data)])
        return fp.pdf_producer_unexpected(ctx)

    def test_wrong_producer_flags_with_evidence(self):
        flags = self._run("Microsoft Print To PDF")
        assert len(flags) == 1
        assert flags[0].severity == "medium"
        assert flags[0].document_ids == ["d1"]
        assert flags[0].evidence == (
            "issuer=dbs (p=0.93); Producer='Microsoft Print To PDF'; expected prefix 'Mock Composer'"
        )

    def test_absent_producer_is_unexpected(self):
        flags = self._run(None)
        assert len(flags) == 1
        assert "Producer absent" in flags[0].evidence

    def test_expected_producer_is_silent(self):
        assert self._run("Mock Composer 9.1") == []
        assert self._run("mock composer 2") == []  # case-insensitive prefix

    def test_tamper_t32c_other_software(self):
        assert len(self._run("Mock Word Processor 3.1")) == 1

    @pytest.mark.parametrize(
        ("label", "producer"),
        [
            ("ocbc", "Mock Exstream 4"),
            ("uob", "Mock FOP 1"),
            ("software_generated", "Mock Ledger 2"),
        ],
    )
    def test_each_issuer_has_its_producer(self, label, producer):
        assert self._run(producer, label=label) == []
        assert len(self._run("Mock Composer 9.1", label=label)) == 1

    def test_low_issuer_probability_is_skipped(self):
        with pytest.raises(CheckSkipped):
            self._run("Microsoft Print To PDF", p=0.50)

    def test_missing_issuer_is_skipped(self):
        with pytest.raises(CheckSkipped):
            self._run("Microsoft Print To PDF", label=None)

    def test_scanned_statement_is_not_evaluated(self):
        assert self._run("Mock Scanner 2.1", scan=True) == []

    def test_issuer_without_known_producer_is_not_evaluated(self):
        assert self._run("Anything", label="other_bank") == []

    def test_probability_threshold_comes_from_settings(self):
        data = _statement(producer="Microsoft Print To PDF")
        ref = file_ref("f1", data)
        doc = statement_doc("d1", pages=1, issuer_p=0.60)
        ctx = make_ctx([doc], files=[(ref, data)])
        with pytest.raises(CheckSkipped):
            fp.pdf_producer_unexpected(ctx)
        ctx.settings = ctx.settings.model_copy(update={"classifier_min_prob": 0.5})
        assert len(fp.pdf_producer_unexpected(ctx)) == 1

    def test_only_bank_statements_are_considered(self):
        data = _statement(producer="Microsoft Print To PDF")
        invoice = make_doc("invoice", id="d1", file_id="f1")
        ctx = make_ctx([invoice], files=[(file_ref("f1", data), data)])
        with pytest.raises(CheckSkipped):
            fp.pdf_producer_unexpected(ctx)


# --------------------------------------------------------------------------------------
# ai_provenance_hint (test 26)
# --------------------------------------------------------------------------------------


def _jpeg(software: str | None = None, *, tail: bytes = b"") -> bytes:
    img = Image.new("RGB", (64, 64), (200, 200, 200))
    buf = io.BytesIO()
    exif = Image.Exif()
    if software:
        exif[305] = software
    img.save(buf, "JPEG", exif=exif)
    return buf.getvalue() + tail


def _png(text: dict[str, str] | None = None) -> bytes:
    from PIL import PngImagePlugin

    info = PngImagePlugin.PngInfo()
    for k, v in (text or {}).items():
        info.add_text(k, v)
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), (10, 10, 10)).save(buf, "PNG", pnginfo=info)
    return buf.getvalue()


def _image_ctx(fid: str, data: bytes, mime: str):
    from app.core.models import FileRef

    ref = FileRef(id=fid, name=f"{fid}.img", sha256="0" * 64, size_bytes=len(data), mime=mime)
    return make_ctx([make_doc("invoice", id="d1", file_id=fid)], files=[(ref, data)])


class TestAiProvenanceHint:
    def test_registered_exactly_once(self):
        meta = reg.get_check("ai_provenance_hint")
        assert meta is not None
        assert meta.severity == "info"
        assert meta.stage == "forensics"

    def test_pdf_info_marker(self):
        data = _statement(producer="Midjourney V6 export")
        flags = fp.ai_provenance_hint(ctx_for([("f1", data)]))
        assert len(flags) == 1
        assert flags[0].severity == "info"
        assert "marker 'Midjourney' in PDF Info Producer" in flags[0].evidence

    def test_pdf_xmp_digital_source_type(self):
        data = set_meta(
            _statement(),
            Producer=GOOD_PRODUCER,
            xmp={
                "dc:description": "http://cv.iptc.org/newscodes/digitalsourcetype/trainedAlgorithmicMedia"
            },
        )
        flags = fp.ai_provenance_hint(ctx_for([("f1", data)]))
        assert len(flags) == 1
        assert "trainedAlgorithmicMedia" in flags[0].evidence
        assert "XMP" in flags[0].evidence

    def test_c2pa_marker_bytes(self):
        data = _statement() + b"\n% c2pa manifest store jumb box\n"
        flags = fp.ai_provenance_hint(ctx_for([("f1", data)]))
        assert len(flags) == 1
        assert "content credentials (C2PA manifest) present" in flags[0].evidence

    def test_jpeg_exif_software(self):
        flags = fp.ai_provenance_hint(_image_ctx("f1", _jpeg("Midjourney V6"), "image/jpeg"))
        assert len(flags) == 1
        assert "marker 'Midjourney' in EXIF Software" in flags[0].evidence

    def test_png_text_chunk(self):
        data = _png({"parameters": "a cat, Steps: 20, Model: Stable Diffusion XL"})
        flags = fp.ai_provenance_hint(_image_ctx("f1", data, "image/png"))
        assert len(flags) == 1
        assert "PNG text parameters" in flags[0].evidence

    def test_clean_files_ran_and_nothing_found(self):
        assert fp.ai_provenance_hint(ctx_for([("f1", _statement())])) == []
        assert fp.ai_provenance_hint(_image_ctx("f1", _jpeg("Canon EOS R5"), "image/jpeg")) == []
        assert (
            fp.ai_provenance_hint(_image_ctx("f1", _png({"Comment": "hello"}), "image/png")) == []
        )

    def test_wording_never_claims_authenticity(self):
        data = _statement(producer="Stable Diffusion")
        flag = fp.ai_provenance_hint(ctx_for([("f1", data)]))[0]
        text = (flag.evidence + " " + flag.explanation).lower()
        for word in ("authentic", "genuine", "verified"):
            assert word not in text
        assert "hint" in text
        assert "proves nothing" in text

    def test_skipped_without_files(self):
        with pytest.raises(CheckSkipped):
            fp.ai_provenance_hint(make_ctx([make_doc("invoice")]))

    def test_does_not_register_a_second_time_when_another_module_owns_it(self, monkeypatch):
        """forensics_image.py may own this rule (docs/forensics.md 3.1); registry.check refuses
        two functions for one rule id, so this module steps aside when it sees an owner."""
        owner = reg.CheckMeta(
            rule="ai_provenance_hint",
            stage="forensics",
            severity="info",
            applies_to=frozenset({"*"}),
            description="other module",
            func=_other_module_check,
        )
        monkeypatch.setitem(reg._REGISTRY, "ai_provenance_hint", owner)
        assert fp._owned_elsewhere("ai_provenance_hint") is True

    def test_owned_elsewhere_is_false_for_own_registration(self):
        meta = reg.get_check("ai_provenance_hint")
        if meta is not None and meta.func.__module__ == fp.__name__:
            assert fp._owned_elsewhere("ai_provenance_hint") is False
        else:
            pytest.skip("rule is owned by forensics_image in this tree")


def _other_module_check(ctx):  # pragma: no cover - only referenced for its __module__
    return []


_other_module_check.__module__ = "app.checks.forensics_image"


# --------------------------------------------------------------------------------------
# Registration, clean control, determinism, runtime (tests 1, 30, 31; T-forensics-pdf-10)
# --------------------------------------------------------------------------------------


def test_registration_metadata_matches_the_spec():
    metas = reg.checks_by_rule()
    for rule, (severity, func) in PDF_RULES.items():
        meta = metas[rule]
        assert meta.stage == "forensics", rule
        assert meta.severity == severity, rule
        assert meta.applies_to == frozenset({"*"}), rule
        assert meta.requires == frozenset(), rule
        assert meta.use_cases == frozenset({"kyc", "aml", "loan"}), rule
        assert meta.version == "1", rule
        assert meta.func is func, rule
        assert meta.description and meta.tamper, rule


def test_seven_pdf_rules_in_all_checks():
    rules = {m.rule for m in reg.all_checks() if m.stage == "forensics"}
    assert set(PDF_RULES) <= rules


def test_clean_three_page_statement_all_rules_ran_with_zero_flags():
    """Test 1: Helvetica 9 / Helvetica-Bold, Producer Mock Composer, issuer dbs, no signature."""
    ctx = ctx_for([("f1", statement_pdf())])
    runs = reg.run_stage_checks(ctx, "forensics")
    by_rule = {r.rule: r for r in runs}
    for rule in PDF_RULES:
        assert by_rule[rule].status == "ran", (rule, by_rule[rule].reason)
        assert by_rule[rule].flags == 0, rule
    mine = [f for f in ctx.case.flags if f.rule in PDF_RULES]
    assert mine == []


def test_clean_statement_has_the_honest_quirks_the_rules_must_tolerate():
    import pymupdf

    data = statement_pdf()
    doc = pymupdf.open(stream=data, filetype="pdf")
    fonts = {
        s["font"]
        for page in doc
        for b in page.get_text("dict")["blocks"]
        for line in b["lines"]
        for s in line["spans"]
    }
    assert fonts == {"Helvetica", "Helvetica-Bold"}
    assert len(page_infos(data)) == 3
    assert any(d["fill"] and abs(d["fill"][0] - 0.85) < 0.01 for d in doc[0].get_drawings())


def test_failing_file_is_an_error_not_a_crash():
    ctx = ctx_for([("f1", statement_pdf(pages=1, rows_per_page=6))])
    ctx.file_path("f1").write_bytes(b"not a pdf at all")
    runs = reg.run_stage_checks(ctx, "forensics")
    by_rule = {r.rule: r for r in runs}
    assert by_rule["pdf_text_vs_render"].status == "error"
    assert by_rule["font_mix"].status == "error"


def test_results_are_deterministic_and_order_independent():
    from tests.forensics_pdf_factory import white_box_retype

    clean = statement_pdf()
    tampered = white_box_retype(
        clean, 2, _first_amount(clean, 2), "15,500.00", fontname="cour", fontsize=9.4
    )

    def run(order):
        ctx = ctx_for([("f1", tampered)])
        out = {}
        for rule in order:
            out[rule] = [f.model_dump_json() for f in PDF_RULES[rule][1](ctx)]
        return out

    rules = list(PDF_RULES)
    first = run(rules)
    assert first == run(rules)
    assert first == run(list(reversed(rules)))
    assert first["pdf_text_vs_render"], "the tamper must be seen"


def _first_amount(data: bytes, page: int) -> str:
    import pymupdf

    words = pymupdf.open(stream=data, filetype="pdf")[page - 1].get_text("words")
    return next(w[4] for w in words if "." in w[4] and w[4][0].isdigit())


@pytest.mark.timeout(60)
def test_loose_runtime_ceiling_for_a_ten_page_text_pdf():
    import time

    data = statement_pdf(pages=10)
    ctx = ctx_for([("f1", data)])
    started = time.perf_counter()
    for _severity, func in PDF_RULES.values():
        func(ctx)
    assert time.perf_counter() - started < 20


def test_module_has_no_wall_clock_or_file_writes():
    import ast
    from pathlib import Path

    source = Path(fp.__file__).read_text()
    tree = ast.parse(source)
    banned = {"open_write", "write_bytes", "write_text", "save", "saveIncr", "ez_save"}
    called = {
        n.func.attr
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
    }
    assert not (called & banned), called & banned
    assert "datetime.now" not in source
    assert "date.today" not in source
    assert "time.time" not in source
    assert CREATED  # fixture constant is used by the factory
