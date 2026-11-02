"""pdf_signature_invalid: a real signature made with pyHanko, then edited."""

from __future__ import annotations

import datetime
import io

import pymupdf as fitz
import pytest
from asn1crypto import keys as ak
from asn1crypto import x509 as ax
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.sign import fields, signers

from app.checks.forensics_pdf import signature_problems


@pytest.fixture(scope="module")
def signed_pdf() -> bytes:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test Bank")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(datetime.datetime(2026, 1, 1))
        .not_valid_after(datetime.datetime(2030, 1, 1))
        .sign(key, hashes.SHA256())
    )
    signer = signers.SimpleSigner(
        signing_cert=ax.Certificate.load(cert.public_bytes(serialization.Encoding.DER)),
        signing_key=ak.PrivateKeyInfo.load(
            key.private_bytes(
                serialization.Encoding.DER,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        ),
        cert_registry=None,
    )
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "Closing balance 1,000.00")
    writer = IncrementalPdfFileWriter(io.BytesIO(doc.tobytes()))
    fields.append_signature_field(writer, fields.SigFieldSpec("Sig1"))
    out = io.BytesIO()
    signers.sign_pdf(
        writer, signers.PdfSignatureMetadata(field_name="Sig1"), signer=signer, output=out
    )
    return out.getvalue()


def test_an_untouched_signed_pdf_has_no_problems(signed_pdf: bytes) -> None:
    assert signature_problems(signed_pdf) == []


def test_a_byte_changed_inside_the_signed_range_is_found(signed_pdf: bytes) -> None:
    i = signed_pdf.find(b"/MediaBox")
    edited = signed_pdf[: i + 10] + b"9" + signed_pdf[i + 11 :]
    problems = signature_problems(edited)
    assert problems and "no longer match" in problems[0]


def test_an_unsigned_pdf_has_no_problems() -> None:
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "hello")
    assert signature_problems(doc.tobytes()) == []


def test_garbage_is_a_value_error_not_a_crash() -> None:
    with pytest.raises(ValueError):
        signature_problems(b"%PDF-1.7 not really")


def test_a_signature_that_cannot_be_validated_is_a_gap_not_evidence(
    signed_pdf: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pyhanko.sign.validation as v

    def boom(*a, **k):  # noqa: ANN002, ANN003, ANN202
        raise RuntimeError("unsupported algorithm")

    monkeypatch.setattr(v, "validate_pdf_signature", boom)
    with pytest.raises(ValueError, match="could not be assessed"):
        signature_problems(signed_pdf)
