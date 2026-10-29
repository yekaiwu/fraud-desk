"""pdf_bytes_after_signature and the signed-revision byte-range logic (docs/forensics.md 4.3,
tests 6 to 10). Signed files are written by hand: placeholder /Contents, correct /ByteRange."""

from __future__ import annotations

import os

import pytest

from app.checks import forensics_pdf as fp
from app.checks.registry import CheckSkipped
from app.core.testing import make_ctx, make_doc
from tests.forensics_pdf_factory import (
    append_revision,
    build_signed,
    build_unsigned,
    byteranges,
    ctx_for,
    set_byterange,
    statement_doc,
    statement_pdf,
    tamper_after_signature,
)


def _boom(*args, **kwargs):
    raise fp.pikepdf.PdfError("cannot parse")


def run(data: bytes):
    return fp.pdf_bytes_after_signature(ctx_for([("f1", data)], [statement_doc("d1", pages=1)]))


# --------------------------------------------------------------------------------------
# The factory itself (so a failing test below is not a broken fixture)
# --------------------------------------------------------------------------------------


def test_factory_builds_valid_signed_pdfs():
    import io

    import pikepdf
    import pymupdf

    for n in (1, 2):
        data = build_signed(n)
        assert data.count(b"%%EOF") == 1 + n
        ranges = byteranges(data)
        assert len(ranges) == n
        assert ranges[-1][2] + ranges[-1][3] == len(data)
        assert len(pikepdf.open(io.BytesIO(data)).pages) == 1
        assert "Balance 1,000.00" in pymupdf.open(stream=data, filetype="pdf")[0].get_text()


# --------------------------------------------------------------------------------------
# signature_ranges / signed_revision_report
# --------------------------------------------------------------------------------------


class TestSignatureRanges:
    def test_no_signature(self):
        assert fp.signature_ranges(build_unsigned()) == []
        report = fp.signed_revision_report(build_unsigned())
        assert report.ranges == []
        assert report.malformed == []
        assert report.last_signed_end is None
        assert report.trailing == 0

    def test_two_sequential_signatures(self):
        data = build_signed(2)
        ranges = fp.signature_ranges(data)
        assert [r.index for r in ranges] == [0, 1]
        assert all(r.a == 0 and r.source == "pikepdf" for r in ranges)
        assert all(r.signed_end == r.c + r.d for r in ranges)
        assert ranges[0].signed_end < ranges[1].signed_end == len(data)

    def test_report_of_an_honest_signed_file(self):
        data = build_signed(2)
        report = fp.signed_revision_report(data)
        assert report.malformed == []
        assert report.last_signed_end == len(data)
        assert report.trailing == 0
        assert report.trailing_is_whitespace_only is False

    def test_report_is_a_pure_function_of_the_bytes(self):
        data = build_signed(1)
        assert fp.signed_revision_report(data) == fp.signed_revision_report(bytes(data))

    def test_trailing_whitespace_flag(self):
        report = fp.signed_revision_report(build_signed(1) + b"\r\n")
        assert report.trailing == 2
        assert report.trailing_is_whitespace_only is True
        assert (
            fp.signed_revision_report(build_signed(1) + b"xy").trailing_is_whitespace_only is False
        )


# --------------------------------------------------------------------------------------
# Test 6: honest signed files do not flag
# --------------------------------------------------------------------------------------


class TestHonestSignedFiles:
    def test_signed_without_trailing_bytes(self):
        assert run(build_signed(1)) == []

    def test_two_sequential_signatures_with_three_eof_markers(self):
        data = build_signed(2)
        assert data.count(b"%%EOF") == 3
        assert run(data) == []

    def test_unsigned_pdf_with_several_eof_markers_is_not_a_signal(self):
        """A bare %%EOF count is not the rule: an unsigned incrementally saved file is silent."""
        data = append_revision(build_unsigned(), {7: b"<< /Type /Foo >>"}, size=8)
        assert data.count(b"%%EOF") == 2
        assert run(data) == []

    def test_unsigned_reportlab_file_ran_with_no_flag(self):
        assert run(statement_pdf(pages=1, rows_per_page=6)) == []


# --------------------------------------------------------------------------------------
# Test 7: appended incremental update
# --------------------------------------------------------------------------------------


class TestAppendedUpdate:
    def test_flag_is_high_with_bytes_startxref_and_render_difference(self):
        signed = build_signed(1)
        data = tamper_after_signature(signed)
        trailing = len(data) - len(signed)
        flags = run(data)
        assert len(flags) == 1
        flag = flags[0]
        assert flag.rule == "pdf_bytes_after_signature"
        assert flag.severity == "high"
        assert flag.document_ids == ["d1"]
        assert flag.id == "pdf_bytes_after_signature.d1.-~f1"
        assert f"{trailing} bytes follow the last signed revision" in flag.evidence
        assert f"signature 1 covers [0,{len(signed)}) of {len(data)}" in flag.evidence
        assert "startxref" in flag.evidence
        assert "render differs on page 1" in flag.evidence
        assert flag.explanation.startswith(f"{trailing} bytes follow")

    def test_region_marks_where_the_page_changed(self):
        flag = run(tamper_after_signature(build_signed(1)))[0]
        assert flag.region is not None
        assert flag.region.page == 1
        assert flag.region.label == "changed after signature"
        # the text sits at the top-left of a 612 x 792 page (72, 700)
        assert flag.region.x < 0.25
        assert flag.region.y < 0.2

    def test_update_without_visible_change_is_still_a_flag(self):
        signed = build_signed(1)
        data = append_revision(signed, {20: b"<< /Type /Foo >>"}, size=21)
        flag = run(data)[0]
        assert flag.severity == "high"
        assert "render unchanged" in flag.evidence
        assert flag.region is None

    def test_second_signature_then_tamper(self):
        data = tamper_after_signature(build_signed(2))
        flag = run(data)[0]
        assert "signature 2 covers" in flag.evidence

    def test_all_documents_of_the_file_are_named(self):
        data = tamper_after_signature(build_signed(1))
        docs = [
            make_doc("bank_statement", id="d1", file_id="f1"),
            make_doc("invoice", id="d2", file_id="f1"),
            make_doc("invoice", id="d3", file_id="f2"),
        ]
        ctx = ctx_for([("f1", data)], docs)
        assert fp.pdf_bytes_after_signature(ctx)[0].document_ids == ["d1", "d2"]


# --------------------------------------------------------------------------------------
# Test 8: trailing bytes
# --------------------------------------------------------------------------------------


class TestTrailingBytes:
    @pytest.mark.parametrize("tail", [b"\n", b"\r\n", b"\r"])
    def test_one_or_two_eol_bytes_are_clean(self, tail):
        assert run(build_signed(1) + tail) == []

    def test_forty_bytes_of_garbage_flag_without_startxref(self):
        flags = run(build_signed(1) + b"G" * 40)
        assert len(flags) == 1
        assert "40 bytes follow the last signed revision" in flags[0].evidence
        assert "no startxref" in flags[0].evidence
        assert flags[0].severity == "high"
        assert "render unchanged" in flags[0].evidence

    def test_three_whitespace_bytes_flag(self):
        assert len(run(build_signed(1) + b"   ")) == 1

    def test_two_non_whitespace_bytes_flag(self):
        assert len(run(build_signed(1) + b"xy")) == 1

    def test_earlier_signature_stopping_before_a_later_revision_is_fine(self):
        data = build_signed(2)
        first = fp.signature_ranges(data)[0]
        assert first.signed_end < len(data)
        assert fp.signed_revision_report(data).malformed == []


# --------------------------------------------------------------------------------------
# Test 9: malformed ByteRange variants
# --------------------------------------------------------------------------------------


class TestMalformed:
    def test_range_not_starting_at_zero(self):
        data = build_signed(1)
        a, b, c, d = byteranges(data)[0]
        bad = set_byterange(data, 0, (5, b, c, d))
        report = fp.signed_revision_report(bad)
        assert any("does not start at 0" in m for m in report.malformed)
        flags = run(bad)
        assert len(flags) == 1
        assert flags[0].severity == "high"
        assert flags[0].evidence.startswith("malformed signature byte range:")

    def test_range_longer_than_the_file(self):
        data = build_signed(1)
        a, b, c, d = byteranges(data)[0]
        bad = set_byterange(data, 0, (a, b, c, d + 500))
        report = fp.signed_revision_report(bad)
        assert any("beyond the end of the file" in m for m in report.malformed)
        flags = run(bad)
        assert len(flags) == 1
        assert flags[0].severity == "high"
        assert "malformed" in flags[0].evidence

    def test_file_ends_before_the_signed_range(self):
        data = build_signed(1)
        _a, _b, c, _d = byteranges(data)[0]
        truncated = data[: c + 20]
        report = fp.signed_revision_report(truncated)
        assert report.ranges
        assert any("beyond the end of the file" in m for m in report.malformed)
        assert len(run(truncated)) == 1

    def test_gap_not_delimited_by_angle_brackets(self):
        data = build_signed(1)
        a, b, c, d = byteranges(data)[0]
        bad = set_byterange(data, 0, (a, b + 1, c, d))
        report = fp.signed_revision_report(bad)
        assert any("not a hex string delimited by < and >" in m for m in report.malformed)
        assert len(run(bad)) == 1

    def test_gap_that_is_not_hex(self):
        data = build_signed(1)
        a, b, c, d = byteranges(data)[0]
        at = data.index(b"<", b) + 1
        bad = data[:at] + b"ZZ" + data[at + 2 :]
        assert any("hex" in m for m in fp.signed_revision_report(bad).malformed)

    def test_second_gap_inside_the_first_signed_range(self):
        data = build_signed(2)
        first, second = byteranges(data)
        bad = set_byterange(data, 1, (second[0], first[1] - 3, first[2], second[3]))
        report = fp.signed_revision_report(bad)
        assert any("gap overlaps signed bytes of signature 1" in m for m in report.malformed)
        flags = run(bad)
        assert len(flags) == 1
        assert flags[0].severity == "high"

    def test_empty_gap(self):
        data = build_signed(1)
        a, b, c, d = byteranges(data)[0]
        bad = set_byterange(data, 0, (a, c, c, d))
        assert any("empty or reversed" in m for m in fp.signed_revision_report(bad).malformed)

    def test_byterange_with_three_numbers_is_bad_syntax(self):
        data = build_signed(1)
        a, b, c, d = byteranges(data)[0]
        old = f"/ByteRange [{a} {b:010d} {c:010d} {d:010d}]".encode()
        new = (
            f"/ByteRange [{a} {b:010d} {c:010d}".encode()
            + b" " * (len(old) - len(f"/ByteRange [{a} {b:010d} {c:010d}") - 1)
            + b"]"
        )
        assert len(new) == len(old)
        bad = data.replace(old, new)
        report = fp.signed_revision_report(bad)
        assert "bad ByteRange syntax" in report.malformed
        assert len(run(bad)) == 1

    def test_malformed_and_trailing_bytes_are_both_reported(self):
        data = build_signed(1)
        a, b, c, d = byteranges(data)[0]
        bad = set_byterange(data, 0, (7, b, c, d)) + b"G" * 40
        flag = run(bad)[0]
        assert "malformed signature byte range" in flag.evidence
        assert "40 bytes follow the last signed revision" in flag.evidence


# --------------------------------------------------------------------------------------
# Test 10: regex fallback when pikepdf cannot parse the file
# --------------------------------------------------------------------------------------


class TestRegexFallback:
    def test_regex_used_when_pikepdf_raises(self, monkeypatch):
        data = build_signed(2)
        ctx = ctx_for([("f1", data)], [statement_doc("d1", pages=1)])
        monkeypatch.setattr(fp.pikepdf, "open", _boom)
        ranges = fp.signature_ranges(data)
        assert len(ranges) == 2
        assert {r.source for r in ranges} == {"regex"}
        assert fp.signed_revision_report(data).malformed == []
        assert fp.pdf_bytes_after_signature(ctx) == []

    def test_regex_fallback_still_finds_trailing_bytes(self, monkeypatch):
        ctx = ctx_for([("f1", build_signed(1) + b"G" * 40)], [statement_doc("d1", pages=1)])
        monkeypatch.setattr(fp.pikepdf, "open", _boom)
        flags = fp.pdf_bytes_after_signature(ctx)
        assert len(flags) == 1
        assert "40 bytes follow" in flags[0].evidence

    def test_truncated_file_that_pikepdf_cannot_trust_still_yields_ranges(self):
        data = build_signed(1)
        cut = data[: data.rindex(b"\nxref\n")]  # the signature object is there, the xref is not
        assert not cut.rstrip().endswith(b"%%EOF")
        ranges = fp.signature_ranges(cut)
        assert len(ranges) == 1
        assert ranges[0].source in ("pikepdf", "regex")
        assert (
            len(
                fp.pdf_bytes_after_signature(ctx_for([("f1", cut)], [statement_doc("d1", pages=1)]))
            )
            == 1
        )

    def test_superseded_signature_object_is_still_seen_by_regex(self):
        """pikepdf only sees live objects; a signature dictionary replaced by a later update is
        still in the bytes, and the regex pass keeps it."""
        signed = build_signed(1)
        replaced = append_revision(signed, {7: b"<< /Type /Foo >>"}, size=8)
        live = {(r.a, r.b, r.c, r.d): r.source for r in fp.signature_ranges(replaced)}
        assert (0, *byteranges(signed)[0][1:]) in live


# --------------------------------------------------------------------------------------
# Check-level behaviour
# --------------------------------------------------------------------------------------


class TestCheckBehaviour:
    def test_skipped_without_pdf(self):
        with pytest.raises(CheckSkipped):
            fp.pdf_bytes_after_signature(make_ctx([make_doc("invoice")]))

    def test_unreadable_files_are_an_error(self):
        ctx = ctx_for([("f1", build_signed(1))], [statement_doc("d1", pages=1)])
        os.remove(ctx.file_path("f1"))
        with pytest.raises(RuntimeError, match="all candidate files unreadable"):
            fp.pdf_bytes_after_signature(ctx)

    def test_some_unreadable_files_add_a_limit_notice(self):
        good = build_signed(1) + b"G" * 40
        ctx = ctx_for(
            [("f1", good), ("f2", build_signed(1))],
            [
                statement_doc("d1", file_id="f1", pages=1),
                statement_doc("d2", file_id="f2", pages=1),
            ],
        )
        os.remove(ctx.file_path("f2"))
        flags = fp.pdf_bytes_after_signature(ctx)
        assert [f.severity for f in flags] == ["high", "info"]
        assert "f2" in flags[1].evidence
        assert flags[1].id.endswith("~notice")

    def test_one_flag_per_file(self):
        bad = tamper_after_signature(build_signed(1))
        ctx = ctx_for(
            [("f1", bad), ("f2", bad + b"!")],
            [
                statement_doc("d1", file_id="f1", pages=1),
                statement_doc("d2", file_id="f2", pages=1),
            ],
        )
        flags = fp.pdf_bytes_after_signature(ctx)
        assert [f.document_ids for f in flags] == [["d1"], ["d2"]]

    def test_severity_is_always_high(self):
        flag = run(build_signed(1) + b"G" * 3)[0]
        assert flag.severity == "high"
