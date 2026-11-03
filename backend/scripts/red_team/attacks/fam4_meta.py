"""Family 4: provenance. Rebuilding a file, scrubbing or spoofing its metadata, incremental
updates, and signatures a forger can produce himself."""

from __future__ import annotations

import io
import random
import re
from datetime import UTC, datetime
from decimal import Decimal

import pikepdf
from pikepdf import Dictionary, Name

from ..core import Bundle, Outcome, attack
from ..pdfkit import fmt_money
from ._util import credits, invoice, stmts

F = "F4 provenance and metadata"
INV = "invoice_INV-2025-0107.pdf"


def _forge_invoice(b: Bundle, name: str = INV, mult: float = 1.6) -> str:
    inv = invoice(b, name)
    for k in range(len(inv.lines)):
        inv.set_qty(k, int(int(inv.lines[k][1].text) * mult))
    inv.recompute()
    b.replace(name, inv.dump())
    return fmt_money(inv.recompute()[2])


def _forge_statements_neutral(b: Bundle) -> list[str]:
    """R02's balance-neutral window dressing, returns the touched file names."""
    ss = stmts(b)
    bump = Decimal("48000")
    row = credits(ss, 1)[0]
    ss.stmts[1].set_amount(row, row.amount + bump)
    d = [r for r in ss.stmts[5].rows if r.kind == "wd" and r.amount > Decimal("5000")][0]
    ss.stmts[5].set_amount(d, d.amount + bump)
    ss.recompute_from(1)
    ss.write_back(b.files)
    return [s.name for s in ss.stmts[1:]]


def _set_info(data: bytes, **kv) -> bytes:
    pdf = pikepdf.open(io.BytesIO(data))
    for k in list(pdf.docinfo.keys()):
        if k in ("/Producer", "/Creator", "/ModDate", "/CreationDate"):
            del pdf.docinfo[k]
    for k, v in kv.items():
        if v is not None:
            pdf.docinfo["/" + k] = v
    out = io.BytesIO()
    pdf.save(out)
    return out.getvalue()


def _info(data: bytes) -> dict:
    pdf = pikepdf.open(io.BytesIO(data))
    return {str(k)[1:]: str(v) for k, v in pdf.docinfo.items()}


@attack(
    "R30",
    F,
    "Statements rebuilt as brand-new PDF files (fresh object tree) carrying the issuer's own Producer, Creator and dates",
    consistent=True,
)
def r30(b: Bundle, rng: random.Random) -> Outcome:
    originals = {n: b.files[n] for n in b.files if n.startswith("bank_statement_")}
    touched = _forge_statements_neutral(b)
    for n in touched:
        old = pikepdf.open(io.BytesIO(b.files[n]))
        new = pikepdf.new()
        info = _info(originals[n])
        for pg in old.pages:
            fonts = Dictionary(
                F1=new.make_indirect(
                    Dictionary(
                        Type=Name.Font,
                        Subtype=Name.Type1,
                        BaseFont=Name.Helvetica,
                        Encoding=Name.WinAnsiEncoding,
                    )
                ),
                F2=new.make_indirect(
                    Dictionary(
                        Type=Name.Font,
                        Subtype=Name.Type1,
                        BaseFont=Name("/Helvetica-Bold"),
                        Encoding=Name.WinAnsiEncoding,
                    )
                ),
            )
            page = new.add_blank_page(page_size=(float(pg.MediaBox[2]), float(pg.MediaBox[3])))
            page.Resources = Dictionary(Font=fonts)
            page.Contents = new.make_stream(pg.Contents.read_bytes())
        for k, v in info.items():
            new.docinfo["/" + k] = v
        out = io.BytesIO()
        new.save(out)
        b.replace(n, out.getvalue())
    return Outcome(
        "Build each edited statement again from nothing, with the bank's own producer string (public on every genuine statement).",
        touched,
    )


@attack("R31", F, "Metadata scrub on a forged invoice: no Info dictionary, no XMP")
def r31(b: Bundle, rng: random.Random) -> Outcome:
    tot = _forge_invoice(b)
    pdf = pikepdf.open(io.BytesIO(b.files[INV]))
    for k in list(pdf.docinfo.keys()):
        del pdf.docinfo[k]
    out = io.BytesIO()
    pdf.save(out)
    b.replace(INV, out.getvalue())
    return Outcome("Strip all document information from an inflated invoice.", [INV], [tot])


@attack("R32", F, "Metadata scrub on forged bank statements (producer absent)")
def r32(b: Bundle, rng: random.Random) -> Outcome:
    touched = _forge_statements_neutral(b)
    for n in touched:
        pdf = pikepdf.open(io.BytesIO(b.files[n]))
        for k in list(pdf.docinfo.keys()):
            del pdf.docinfo[k]
        out = io.BytesIO()
        pdf.save(out)
        b.replace(n, out.getvalue())
    return Outcome("Strip all document information from the edited statements.", touched)


def _mod_date(info: dict, days: int = 3) -> str:
    d = datetime.now(UTC).replace(microsecond=0)
    return d.strftime("D:%Y%m%d%H%M%S+00'00'")


@attack(
    "R33",
    F,
    "Invoice edited in Acrobat: Producer 'Adobe PDF Library 24.2', Creator 'Acrobat PDFMaker 24 for Word'",
)
def r33(b: Bundle, rng: random.Random) -> Outcome:
    tot = _forge_invoice(b)
    info = _info(b.files[INV])
    b.replace(
        INV,
        _set_info(
            b.files[INV],
            Producer="Adobe PDF Library 24.2.52",
            Creator="Acrobat PDFMaker 24 for Word",
            CreationDate=info["CreationDate"],
            ModDate=info["CreationDate"],
        ),
    )
    return Outcome(
        "The strings real Acrobat writes after an edit, without the words 'Adobe Acrobat'.",
        [INV],
        [tot],
    )


@attack(
    "R34",
    F,
    "Invoice edited in a desktop PDF editor not on any list: Producer 'PDF Expert 3.9 (macOS)'",
)
def r34(b: Bundle, rng: random.Random) -> Outcome:
    tot = _forge_invoice(b)
    info = _info(b.files[INV])
    b.replace(
        INV,
        _set_info(
            b.files[INV],
            Producer="PDF Expert 3.9 (macOS)",
            Creator="PDF Expert",
            CreationDate=info["CreationDate"],
            ModDate=info["CreationDate"],
        ),
    )
    return Outcome("An editor missing from the editor list, dates left equal.", [INV], [tot])


@attack(
    "R35",
    F,
    "Edited bank statements keep the issuer's Producer but ModDate is moved 3 days on and Producer is spoofed per issuer",
)
def r35(b: Bundle, rng: random.Random) -> Outcome:
    touched = _forge_statements_neutral(b)
    for n in touched:
        info = _info(b.files[n])
        b.replace(
            n,
            _set_info(
                b.files[n],
                Producer=info["Producer"],
                Creator=info["Creator"],
                CreationDate=info["CreationDate"],
                ModDate=info["CreationDate"].replace("0900", "0912"),
            ),
        )
    return Outcome(
        "Same producer, modification time a few minutes after creation (inside the 300 s gap it would not matter, here 12 minutes).",
        touched,
    )


def incremental_replace_stream(data: bytes, page_no: int, new_content: bytes) -> bytes:
    """Append a revision that replaces one page's content stream (classic xref, no signature)."""
    pdf = pikepdf.open(io.BytesIO(data))
    page = pdf.pages[page_no]
    num, gen = page.Contents.objgen
    size = int(pdf.trailer.Size)
    startx = int(re.findall(rb"startxref\s+(\d+)", data)[-1])
    trailer_tail = re.findall(rb"trailer\s*<<(.*?)>>\s*startxref", data, re.S)[-1]
    body = data
    if not body.endswith(b"\n"):
        body += b"\n"
    off = len(body)
    obj = (
        b"%d %d obj\n<< /Length %d >>\nstream\n" % (num, gen, len(new_content))
        + new_content
        + b"\nendstream\nendobj\n"
    )
    xref_off = off + len(obj)
    xref = b"xref\n%d 1\n%010d %05d n \n" % (num, off, gen)
    trailer = re.sub(rb"/Size\s+\d+", b"/Size %d" % size, trailer_tail)
    trailer = re.sub(rb"/Prev\s+\d+", b"", trailer)
    tr = b"trailer\n<<" + trailer + b" /Prev %d >>\nstartxref\n%d\n%%%%EOF\n" % (startx, xref_off)
    return body + obj + xref + tr


@attack(
    "R36",
    F,
    "Incremental update on an unsigned statement: forged content stream appended as a new revision, Info untouched",
)
def r36(b: Bundle, rng: random.Random) -> Outcome:
    orig = dict(b.files)
    touched = _forge_statements_neutral(b)
    for n in touched:
        forged = pikepdf.open(io.BytesIO(b.files[n]))
        data = orig[n]
        for pno in range(len(forged.pages)):
            data = incremental_replace_stream(data, pno, forged.pages[pno].Contents.read_bytes())
        b.replace(n, data)
    return Outcome(
        "Keep the genuine file bytes and append the edited first page as a revision; creation/modification dates stay equal.",
        touched,
    )


def _self_signed(data: bytes) -> bytes:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
    from pyhanko.sign import signers
    from pyhanko.sign.fields import SigFieldSpec, append_signature_field
    from pyhanko_certvalidator.registry import SimpleCertificateStore  # noqa: F401

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "Mock Bank Document Signing (self-issued)")]
    )
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime(2025, 1, 1))
        .not_valid_after(datetime(2030, 1, 1))
        .sign(key, hashes.SHA256())
    )
    from asn1crypto import keys as ak
    from asn1crypto import x509 as ax

    signer = signers.SimpleSigner(
        signing_cert=ax.Certificate.load(cert.public_bytes(serialization.Encoding.DER)),
        signing_key=ak.PrivateKeyInfo.load(
            key.private_bytes(
                serialization.Encoding.DER,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        ),
        cert_registry=SimpleCertificateStore(),
    )
    w = IncrementalPdfFileWriter(io.BytesIO(data))
    append_signature_field(w, SigFieldSpec("Sig1"))
    out = io.BytesIO()
    signers.sign_pdf(w, signers.PdfSignatureMetadata(field_name="Sig1"), signer=signer, output=out)
    return out.getvalue()


@attack(
    "R37",
    F,
    "The forger signs the edited statement himself with a self-issued certificate (valid cryptographically, untrusted)",
)
def r37(b: Bundle, rng: random.Random) -> Outcome:
    touched = _forge_statements_neutral(b)
    for n in touched:
        b.replace(n, _self_signed(b.files[n]))
    return Outcome(
        "Add a perfectly valid signature of his own so the file looks 'signed'.", touched
    )


@attack(
    "R38",
    F,
    "A real signature over the genuine invoice, then a forged page appended after it (calibration)",
    sanity=True,
)
def r38(b: Bundle, rng: random.Random) -> Outcome:
    signed = _self_signed(b.files[INV])
    dict(b.files)
    _forge_invoice(b)
    pdf = pikepdf.open(io.BytesIO(b.files[INV]))
    forged = pdf.pages[0].Contents.read_bytes()
    b.replace(INV, incremental_replace_stream(signed, 0, forged))
    return Outcome(
        "Sign first, edit after: the signature must break or the trailing bytes must show.", [INV]
    )


def _kyc_statements_neutral(b: Bundle) -> list[str]:
    ss = stmts(b)
    bump = Decimal("30000")
    row = [r for r in ss.stmts[0].rows if r.kind == "dep" and r.amount >= 1000][0]
    ss.stmts[0].set_amount(row, row.amount + bump)
    d = [r for r in ss.stmts[2].rows if r.kind == "wd" and r.amount > 3000][0]
    ss.stmts[2].set_amount(d, d.amount + bump)
    ss.recompute_from(0)
    ss.write_back(b.files)
    return [s.name for s in ss.stmts]


def _strip(b: Bundle, names: list[str]) -> None:
    from ._util import strip_signatures

    for n in names:
        pdf = pikepdf.open(io.BytesIO(b.files[n]))
        strip_signatures(pdf)
        out = io.BytesIO()
        pdf.save(out)
        b.replace(n, out.getvalue())


@attack(
    "R39",
    F,
    "Signed statements edited and their signatures stripped first (all three months)",
    scenario="kyc",
    consistent=True,
)
def r39(b: Bundle, rng: random.Random) -> Outcome:
    touched = _kyc_statements_neutral(b)
    _strip(b, touched)
    return Outcome("Remove the signature, then edit: nothing is left to break.", touched)


@attack(
    "R39b",
    F,
    "One statement edited inside its own month (credit and debit both +S$30,000, closing unchanged) and only that file's signature stripped; the siblings stay signed",
    scenario="kyc",
    consistent=True,
)
def r39b(b: Bundle, rng: random.Random) -> Outcome:
    ss = stmts(b)
    s = ss.stmts[1]
    row = [r for r in s.rows if r.kind == "dep" and r.amount >= 1000][0]
    deb = [
        r
        for r in s.rows
        if r.kind == "wd" and r.amount > 3000 and s.rows.index(r) > s.rows.index(row)
    ][0]
    s.set_amount(row, row.amount + Decimal("30000"))
    s.set_amount(deb, deb.amount + Decimal("30000"))
    s.recompute()
    ss.write_back(b.files)
    _strip(b, [s.name])
    return Outcome("One unsigned statement among signed siblings of the same account.", [s.name])
