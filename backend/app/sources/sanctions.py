"""Screening against a local snapshot of the OFFICIAL sanctions lists (docs/verification-sources.md).

The snapshot is built by ``backend/scripts/update_sanctions.py`` from the UN Security Council
consolidated list, the OFAC SDN list, the EU consolidated financial sanctions list and the UK Sanctions
List. Screening itself never touches the network.

The matcher is deliberately a HIGH-PRECISION one, set for few false alarms rather than for catching every
spelling, because a name is a weak key and the groups of people who share names are not equal in size:

* a person name matches only when the repo matcher (``app.names.match_person``) says ``same`` (the same
  words in any order, joined or split words, known transliteration aliases) on at least two name words
  (three Chinese characters), or says ``probable`` for a single-letter slip in a word of six letters or
  more. The matcher's "initials", "the shorter name is contained in the longer" and "single name"
  verdicts are NOT used: they are right for a registry comparison and wrong for a list of 30,000 names.
* a company name matches when its canonical form (legal-form words removed) is equal, or one word of six
  letters or more is one letter off.
* a match is ``exact`` or ``fuzzy``; a date of birth, an identifier or a nationality only ever raises
  confidence (``strong`` / ``weak`` / ``conflict`` / ``none``), it is never needed to be shown.

A match is never a decision. The check turns it into a flag for a person to adjudicate.
"""

from __future__ import annotations

import json
import re
import threading
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from rapidfuzz.distance import Levenshtein

from app.names import match_person, normalise_company, normalise_id, normalise_person

SCHEMA = "fraud-desk-sanctions/1"
SOURCE_ORDER = ("un", "ofac", "eu", "uk")
SOURCE_LABELS = {
    "un": "UN Security Council consolidated list",
    "ofac": "OFAC SDN list",
    "eu": "EU consolidated financial sanctions list",
    "uk": "UK Sanctions List",
}
MIN_PERSON_WORDS = 2
MIN_CJK_CHARS = 3
MIN_SLIP_WORD = 6  # a one-letter slip counts only in a word at least this long
MIN_COMPANY_EXACT_CHARS = 10  # a one-word company name this short is too common to match alone
MIN_ID_CHARS = 6

Strength = Literal["strong", "weak", "conflict", "none"]


# ------------------------------------------------------------------------------------ snapshot


class SnapshotError(Exception):
    """The snapshot file is missing, unreadable or not in the expected shape."""


@dataclass(frozen=True)
class Snapshot:
    path: Path
    built_at: datetime
    sources: list[dict[str, Any]]
    entries: list[dict[str, Any]]

    def source(self, source_id: str) -> dict[str, Any] | None:
        return next((s for s in self.sources if s.get("id") == source_id), None)

    def problems(self, now: datetime, max_age_days: int) -> list[str]:
        """Why this snapshot cannot be called a complete, current screen (empty = it can). A source
        that was not downloaded, or whose copy is older than ``max_age_days``, is named."""
        out = []
        limit = timedelta(days=max_age_days)
        for sid in SOURCE_ORDER:
            src = self.source(sid)
            label = SOURCE_LABELS[sid]
            if src is None or src.get("status") not in ("ok", "carried_over"):
                out.append(f"{label} is not in the snapshot")
                continue
            try:
                got = datetime.fromisoformat(str(src["downloaded_at"]))
            except (KeyError, ValueError):
                out.append(f"{label} has no download time")
                continue
            if now - got > limit:
                out.append(
                    f"{label} was downloaded {(now - got).days} days ago (limit {max_age_days})"
                )
        return out


_LOAD_LOCK = threading.Lock()
_LOADED: dict[str, tuple[float, Snapshot]] = {}


def load_snapshot(path: Path) -> Snapshot:
    """Read ``path`` (memoised by modification time). Raises :class:`SnapshotError`."""
    try:
        mtime = path.stat().st_mtime
    except OSError as exc:
        raise SnapshotError(f"no sanctions snapshot at {path.name}") from exc
    with _LOAD_LOCK:
        cached = _LOADED.get(str(path))
        if cached and cached[0] == mtime:
            return cached[1]
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
        if blob.get("schema") != SCHEMA:
            raise ValueError("unknown schema")
        snap = Snapshot(
            path=path,
            built_at=datetime.fromisoformat(blob["built_at"]),
            sources=list(blob["sources"]),
            entries=list(blob["entries"]),
        )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise SnapshotError(f"the sanctions snapshot {path.name} cannot be read") from exc
    with _LOAD_LOCK:
        _LOADED[str(path)] = (mtime, snap)
    return snap


# ------------------------------------------------------------------------------ date and nationality


def _year_of(text: str) -> int | None:
    m = re.match(r"(\d{4})", text)
    return int(m[1]) if m else None


def dob_relation(subject: date | None, entry_dobs: list[str]) -> Strength:
    """``strong``: the full date is listed; ``weak``: the year (or month) fits a listed year, month or
    range; ``conflict``: both sides have a date and none fits; ``none``: one side has no date."""
    if subject is None or not entry_dobs:
        return "none"
    weak = False
    for raw in entry_dobs:
        if "/" in raw:
            lo, _, hi = raw.partition("/")
            if (_year_of(lo) or 0) <= subject.year <= (_year_of(hi) or 0):
                weak = True
        elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
            if raw == subject.isoformat():
                return "strong"
        elif re.fullmatch(r"\d{4}-\d{2}", raw):
            if raw == subject.isoformat()[:7]:
                weak = True
        elif _year_of(raw) == subject.year:
            weak = True
    return "weak" if weak else "conflict"


def nationality_matches(subject: str | None, listed: list[str]) -> bool:
    """A country and its demonym ("Iraqi", "Iraq") match; a coarse test, only ever a secondary one."""
    a = " ".join((subject or "").casefold().split())
    if len(a) < 4:
        return False
    for b in listed:
        short, long_ = sorted((a, b.casefold()), key=len)
        if len(short) >= 4 and long_.startswith(short[: max(4, len(short) - 1)]):
            return True
    return False


# ----------------------------------------------------------------------------------------- matcher


@dataclass(frozen=True)
class Subject:
    """Who is screened. ``kind``: ``person`` or ``entity``. Identifiers are optional."""

    name: str
    kind: Literal["person", "entity"]
    dob: date | None = None
    nationality: str | None = None
    ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class Hit:
    entry: dict[str, Any]
    listed_name: str
    name_match: Literal["exact", "fuzzy", "identifier"]
    dob: Strength
    id_match: bool
    nationality: bool

    @property
    def level(self) -> Literal["high", "medium", "low"]:
        """high: an exact name AND an identifier that agrees (an ID number, a full date of birth, or a
        year of birth together with the nationality). low: an exact name whose listed date of birth
        contradicts the subject's. medium: everything else, a possible match for a person to adjudicate."""
        corroborated = (
            self.id_match or self.dob == "strong" or (self.dob == "weak" and self.nationality)
        )
        if self.name_match == "exact" and corroborated:
            return "high"
        if self.name_match == "exact" and self.dob == "conflict" and not self.id_match:
            return "low"
        return "medium"


def _person_words(name: str) -> tuple[str, ...] | None:
    p = normalise_person(name)
    if p.script == "empty" or p.script == "mixed":
        return None
    need = MIN_CJK_CHARS if p.script == "cjk" else MIN_PERSON_WORDS
    return p.tokens if len(p.tokens) >= need else None


def _company_words(name: str) -> tuple[str, ...] | None:
    canon = normalise_company(name)
    words = tuple(canon.split())
    if not words or (len(words) == 1 and len(canon) < MIN_COMPANY_EXACT_CHARS):
        return None
    return words


def _one_slip(a: tuple[str, ...], b: tuple[str, ...]) -> bool:
    """The same words but one, which is one letter off, in a word of six letters or more."""
    if len(a) != len(b):
        return False
    only_a, only_b = (
        sorted((Counter(a) - Counter(b)).elements()),
        sorted((Counter(b) - Counter(a)).elements()),
    )
    if len(only_a) != 1 or len(only_b) != 1:
        return False
    x, y = only_a[0], only_b[0]
    return min(len(x), len(y)) >= MIN_SLIP_WORD and Levenshtein.distance(x, y) == 1


class Screener:
    """An index over a snapshot's person and entity names. Build once, screen many subjects."""

    def __init__(self, snapshot: Snapshot):
        self.snapshot = snapshot
        self._exact: dict[tuple[str, tuple[str, ...]], list[tuple[int, str]]] = defaultdict(list)
        self._post: dict[tuple[str, int, str], list[tuple[int, str, tuple[str, ...]]]] = (
            defaultdict(list)
        )
        self._ids: dict[str, list[int]] = defaultdict(list)
        for i, e in enumerate(snapshot.entries):
            cls = e["kind"]
            if cls not in ("person", "entity"):
                continue
            for name in e["names"]:
                words = _person_words(name) if cls == "person" else _company_words(name)
                if words is None:
                    continue
                self._exact[(cls, tuple(sorted(words)))].append((i, name))
                for tok in set(words):
                    self._post[(cls, len(words), tok)].append((i, name, words))
            for ident in e.get("ids", []):
                norm = normalise_id(ident.get("number"))
                if len(norm) >= MIN_ID_CHARS and any(c.isdigit() for c in norm):
                    self._ids[norm].append(i)

    # ---- one subject
    def screen(self, subject: Subject, *, limit: int = 3) -> list[Hit]:
        words = (
            _person_words(subject.name)
            if subject.kind == "person"
            else _company_words(subject.name)
        )
        found: dict[int, Hit] = {}
        if words is not None:
            self._by_name(subject, words, found)
        self._by_identifier(subject, found)
        order = {"high": 0, "medium": 1, "low": 2}
        hits = sorted(found.values(), key=lambda h: (order[h.level], h.entry["id"]))
        return hits[:limit]

    def _hit(
        self, subject: Subject, i: int, name: str, how: Literal["exact", "fuzzy", "identifier"]
    ) -> Hit:
        entry = self.snapshot.entries[i]
        subject_ids = {normalise_id(x) for x in subject.ids if x}
        listed_ids = {normalise_id(x.get("number")) for x in entry.get("ids", [])}
        id_match = any(len(s) >= MIN_ID_CHARS and s in listed_ids for s in subject_ids)
        return Hit(
            entry=entry,
            listed_name=name,
            name_match=how,
            dob=dob_relation(subject.dob, entry.get("dob", []))
            if subject.kind == "person"
            else "none",
            id_match=id_match,
            nationality=nationality_matches(subject.nationality, entry.get("nationality", [])),
        )

    def _by_name(self, subject: Subject, words: tuple[str, ...], found: dict[int, Hit]) -> None:
        cls = subject.kind
        for i, name in self._exact.get((cls, tuple(sorted(words))), []):
            if cls == "person" and match_person(subject.name, name).verdict != "same":
                continue  # a patronymic swap ("Ahmad bin Ismail" / "Ismail bin Ahmad") is not one person
            found.setdefault(i, self._hit(subject, i, name, "exact"))
        seen: set[tuple[int, str]] = set()
        n = len(words)
        for tok in set(words):
            for i, name, cand in self._post.get((cls, n, tok), []):
                if i in found or (i, name) in seen:
                    continue
                seen.add((i, name))
                if not _one_slip(words, cand):
                    continue
                if cls == "person":
                    m = match_person(subject.name, name)
                    if not (m.verdict == "probable" and m.slip):
                        continue
                found[i] = self._hit(subject, i, name, "fuzzy")

    def _by_identifier(self, subject: Subject, found: dict[int, Hit]) -> None:
        """An identifier that is on the list while the name is not: the listed party may be using
        another name. Passport and registration numbers only, six characters or more."""
        for raw in subject.ids:
            norm = normalise_id(raw)
            if len(norm) < MIN_ID_CHARS or not any(c.isdigit() for c in norm):
                continue
            for i in self._ids.get(norm, []):
                e = self.snapshot.entries[i]
                if i not in found and e["kind"] == subject.kind:
                    found[i] = self._hit(subject, i, e["names"][0], "identifier")
