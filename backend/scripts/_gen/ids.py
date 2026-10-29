"""Identifier builders: UEN, NRIC/FIN, GST number, ICAO 9303 TD3 MRZ.

* UEN: 9-digit local-company number (``YYYY`` + 5 digits) plus the check letter for which
  ``stdnum.sg.uen.is_valid`` is true. The algorithm is never re-derived (plan 1.4).
* NRIC/FIN: python-stdnum has no NRIC module, so the plan 1.4 algorithm is implemented here and
  pinned to six vectors in the tests.
"""

from __future__ import annotations

import random
import string
from datetime import date

from stdnum.sg import uen as _uen

_NRIC_WEIGHTS = (2, 7, 6, 5, 4, 3, 2)
_ST_LETTERS = "JZIHGFEDCBA"
_FG_LETTERS = "XWUTRQPNMLK"


def uen_check_letter(number9: str) -> str:
    """The check letter that makes ``number9 + letter`` a valid UEN."""
    for letter in string.ascii_uppercase:
        if _uen.is_valid(number9 + letter):
            return letter
    raise ValueError(f"no valid check letter for {number9!r}")


def make_uen(year: int, serial: int) -> str:
    """Local-company UEN ``YYYY`` + 5-digit serial + check letter."""
    number9 = f"{year:04d}{serial % 100000:05d}"
    return number9 + uen_check_letter(number9)


def random_uen(rng: random.Random, year: int) -> str:
    return make_uen(year, rng.randrange(100, 99999))


def nric_check_letter(prefix: str, digits7: str) -> str:
    """Check letter for prefix ``S|T|F|G`` and seven digits."""
    if prefix not in "STFG" or len(prefix) != 1 or len(digits7) != 7 or not digits7.isdigit():
        raise ValueError(f"bad NRIC parts {prefix!r} {digits7!r}")
    total = sum(w * int(d) for w, d in zip(_NRIC_WEIGHTS, digits7, strict=True))
    if prefix in "TG":
        total += 4
    table = _ST_LETTERS if prefix in "ST" else _FG_LETTERS
    return table[total % 11]


def make_nric(prefix: str, digits7: str) -> str:
    return f"{prefix}{digits7}{nric_check_letter(prefix, digits7)}"


def nric_prefix_for_birth(birth_year: int, foreign: bool = False) -> str:
    if foreign:
        return "F" if birth_year < 2000 else "G"
    return "S" if birth_year < 2000 else "T"


def random_nric(rng: random.Random, birth_year: int, foreign: bool = False) -> str:
    digits = f"{rng.randrange(0, 10_000_000):07d}"
    return make_nric(nric_prefix_for_birth(birth_year, foreign), digits)


def is_valid_nric(value: str) -> bool:
    v = (value or "").strip().upper()
    if len(v) != 9 or v[0] not in "STFG" or not v[1:8].isdigit() or not v[8].isalpha():
        return False
    return nric_check_letter(v[0], v[1:8]) == v[8]


def random_gst_no(rng: random.Random) -> str:
    """Printed GST registration format ``M`` + 8 digits + letter. The letter is generated, not
    verified against IRAS (docs gap 8.1)."""
    return f"M{rng.randrange(10_000_000, 99_999_999)}{rng.choice(string.ascii_uppercase)}"


def is_gst_format(value: str) -> bool:
    v = (value or "").strip().upper()
    return len(v) == 10 and v[0] == "M" and v[1:9].isdigit() and v[9].isalpha()


# ------------------------------------------------------------------------------------- MRZ


def _mrz_val(ch: str) -> int:
    if ch.isdigit():
        return int(ch)
    if ch == "<":
        return 0
    return ord(ch.upper()) - 55  # A=10


def mrz_check_digit(text: str) -> str:
    """ICAO 9303 check digit: weights 7,3,1 repeating, mod 10."""
    weights = (7, 3, 1)
    return str(sum(_mrz_val(c) * weights[i % 3] for i, c in enumerate(text)) % 10)


def _mrz_pad(text: str, n: int) -> str:
    cleaned = "".join(c if c.isalnum() else "<" for c in text.upper())
    return (cleaned + "<" * n)[:n]


def mrz_name_field(surname: str, given: str) -> str:
    def clean(s: str) -> str:
        return "<".join("".join(c for c in w.upper() if c.isalpha()) for w in s.split())

    return f"{clean(surname)}<<{clean(given)}"


def mrz_composite(line2: str) -> str:
    """Composite check digit over document number+check, dob+check, expiry+check, personal
    number+check (positions 1-10, 14-20, 22-43)."""
    return mrz_check_digit(line2[0:10] + line2[13:20] + line2[21:43])


def build_td3(
    *,
    issuing: str,
    surname: str,
    given_names: str,
    passport_no: str,
    nationality: str,
    dob: date,
    sex: str,
    expiry: date,
    personal_no: str = "",
) -> tuple[str, str]:
    """Two 44-character TD3 lines with valid ICAO check digits."""
    line1 = ("P<" + _mrz_pad(issuing, 3) + mrz_name_field(surname, given_names) + "<" * 44)[:44]
    doc = _mrz_pad(passport_no, 9)
    dob_s = dob.strftime("%y%m%d")
    exp_s = expiry.strftime("%y%m%d")
    pers = _mrz_pad(personal_no, 14)
    body = (
        doc
        + mrz_check_digit(doc)
        + _mrz_pad(nationality, 3)
        + dob_s
        + mrz_check_digit(dob_s)
        + (sex if sex in "MF" else "<")
        + exp_s
        + mrz_check_digit(exp_s)
        + pers
        + mrz_check_digit(pers)
    )
    return line1, body + mrz_composite(body + "0")


def mrz_line2_valid(line2: str) -> bool:
    if len(line2) != 44:
        return False
    return (
        mrz_check_digit(line2[0:9]) == line2[9]
        and mrz_check_digit(line2[13:19]) == line2[19]
        and mrz_check_digit(line2[21:27]) == line2[27]
        and mrz_check_digit(line2[28:42]) == line2[42]
        and mrz_composite(line2) == line2[43]
    )
