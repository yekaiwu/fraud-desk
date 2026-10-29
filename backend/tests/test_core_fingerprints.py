import hashlib
from pathlib import Path

from app.core.cases import add_file, new_case, sanitise_filename, sniff_mime
from app.core.config import Settings
from app.core.db import Database
from app.core.fingerprints import (
    MemoryFingerprintStore,
    SqliteFingerprintStore,
    file_sha256,
    invoice_fingerprint,
    invoice_key_fingerprint,
)
from app.core.models import Case
from app.core.testing import FIXED_NOW, FIXED_TODAY


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def test_golden_preimages() -> None:
    assert invoice_fingerprint("201912345R", "INV-0042", "2026-03-05", "1090.0") == sha(
        "inv|201912345R|INV-0042|2026-03-05|1090.00"
    )
    assert invoice_key_fingerprint("201912345R", "INV-0042") == sha("invkey|201912345R|INV-0042")


def test_normalisation_keeps_zeros_and_hyphens() -> None:
    a = invoice_fingerprint(" 2019 12345r ", "inv - 0042", "2026-03-05", "1090")
    assert a == invoice_fingerprint("201912345R", "INV-0042", "2026-03-05", "1090.00")
    assert invoice_fingerprint(
        "201912345R", "INV-0042", "2026-03-05", "1090"
    ) != invoice_fingerprint("201912345R", "INV-42", "2026-03-05", "1090")
    assert invoice_fingerprint("U", "001", "2026-03-05", "1") != invoice_fingerprint(
        "U", "1", "2026-03-05", "1"
    )
    assert invoice_fingerprint("U", "1", "2026-03-05", "1.005") == invoice_fingerprint(
        "U", "1", "2026-03-05", "1.01"
    )  # half up to 2 dp
    assert invoice_fingerprint("U", "1", "2026-03-05", "1") != invoice_key_fingerprint("U", "1")


def test_file_sha256(tmp_path: Path) -> None:
    p = tmp_path / "x.bin"
    p.write_bytes(b"hello" * 500_000)
    assert file_sha256(p) == hashlib.sha256(b"hello" * 500_000).hexdigest()


def exercise(store) -> None:  # noqa: ANN001
    store.record("invoice", "abc", case_id="c_1", document_id="d3", meta={"role": "full"})
    store.record(
        "invoice", "abc", case_id="c_1", document_id="d3", meta={"role": "full"}
    )  # idempotent
    store.record("invoice", "abc", case_id="c_2", document_id="d1", meta={"applicant_uen": "X"})
    store.record("file_sha256", "abc", case_id="c_2", document_id=None)
    hits = store.lookup("invoice", "abc")
    assert [(h.case_id, h.document_id) for h in hits] == [("c_1", "d3"), ("c_2", "d1")]
    assert hits[0].meta == {"role": "full"} and hits[1].meta == {"applicant_uen": "X"}
    # a rerun never matches itself
    assert [h.case_id for h in store.lookup("invoice", "abc", exclude_case_id="c_1")] == ["c_2"]
    assert store.lookup("invoice", "nope") == []
    assert [h.kind for h in store.lookup("file_sha256", "abc")] == ["file_sha256"]
    assert store.forget_case("c_2") >= 1
    assert [h.case_id for h in store.lookup("invoice", "abc")] == ["c_1"]


def test_memory_store() -> None:
    exercise(MemoryFingerprintStore())


def test_sqlite_store(db: Database) -> None:
    exercise(SqliteFingerprintStore(db))


def test_cases_helpers(db: Database, settings: Settings) -> None:
    case = new_case(
        settings,
        "kyc",
        reference=" R-1 ",
        applicant_name=" Acme Pte Ltd ",
        applicant_uen=" 2019 12345r ",
        today=FIXED_TODAY,
    )
    assert case.id.startswith("c_") and len(case.id) == 14
    assert case.reference == "R-1" and case.applicant.source == "upload"
    assert case.applicant.uen == "201912345R" and case.status == "processing"
    assert new_case(settings, "loan").applicant.source == "derived"
    assert new_case(Settings(_env_file=None, today=FIXED_TODAY), "loan").today == FIXED_TODAY

    pdf = b"%PDF-1.7 test"
    a = add_file(case, db.files, name="../../etc/passwd\x00.pdf", data=pdf)
    b = add_file(case, db.files, name=None, data=b"\xff\xd8\xff\xe0jpeg")
    assert (a.id, b.id) == ("f1", "f2") and a.mime == "application/pdf" and b.mime == "image/jpeg"
    assert a.name == "passwd.pdf" and b.name == "upload"
    assert db.files.exists(a.sha256)
    import pytest

    with pytest.raises(ValueError):
        add_file(case, db.files, name="x.pdf", data=b"just text")
    assert sniff_mime(b"\x89PNG\r\n") == "image/png" and sniff_mime(b"GIF89a") is None
    assert len(sanitise_filename("a" * 500)) == 120
    assert isinstance(Case.model_validate_json(case.model_dump_json()), Case)
    assert case.created_at >= FIXED_NOW
