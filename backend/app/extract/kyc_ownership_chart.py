"""Ownership chart text-layer extractor (ingest-and-extraction.md 6.8, data-model 6.8).

BEST EFFORT, and the weakest evidence in the system. A chart is a drawing: its text layer gives words
with boxes but not the boxes and arrows around them, so structure is inferred from where the words
sit. The honest limits (also in docs/ingest-and-extraction.md section 11):

* A *node* is a cluster of text lines that are vertically adjacent and horizontally overlapping
  (name, percentage, ID, UBO tag). Two holders drawn very close together can merge; the guard is
  that a node never holds two percentages. Side-by-side text on one line (a name and its percentage
  with a wide gap) is joined afterwards when a percentage-only node has a name-only node to its left.
* The applicant is the topmost node (or the node holding a labelled ``Company name``). Direct holders
  are the next row of nodes; a holder on a deeper row is placed under a company-type node of the row
  above (nearest in x), or under the company named by an explicit ``Held in <company>`` /
  ``via <company>`` line (the generator's boxes print ``Held in``). Parents found from geometry are
  ``source="derived"`` (no verification); printed parents are read text. A
  top-down tree is assumed; an indented outline or a bottom-up chart is not understood.
* ``percent`` is copied as printed. For a holder under an intermediate company it is the percentage
  held **in that parent** (data-model 6.8 [CR-19]); nothing is multiplied here.
* A holder with no readable percentage keeps ``percent=None``. If more than 25% of the holders lack
  one, the chart is not understood and ``LayoutNotRecognised`` sends the page to the model, which
  reads it image-first with no promise of being right.
* ``holder_type`` is the printed marker (``Individual`` / ``Company``); failing that it is derived
  from the ID shape (NRIC/FIN vs UEN) or a legal-form word in the name, marked ``source="derived"``;
  failing that it stays empty. ``is_ubo`` is ``true`` where a UBO tag is printed on the box and
  ``false`` where ``Not UBO`` is printed. On a chart that uses UBO tags anywhere (a badge on some
  box, or a legend line mentioning UBO) a box without a tag is ``false`` with ``source="derived"``
  (it is *not marked*; the check ``kyc_shareholdings`` decides what that means); on a chart with no
  UBO text at all it stays ``None``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from statistics import median

from app.core.models import Cell, Region, Row, Table, Word
from app.extract.base import LayoutNotRecognised
from app.extract.kyc_common import FOOTER, Form, make_validator
from app.extract.registry import register_extractor, register_prompt_hints
from app.extract.text_layer import (
    DocView,
    Seg,
    TextLayerExtractor,
    blank_row,
    row_box,
    words_text,
)

DOC_TYPE = "ownership_chart"

LABELS = {
    "company_name": [
        r"^Applicant(?:\s+Name)?",
        r"^Subject\s+Company",
        r"^(?:Company|Entity)\s+Name",
        r"^Name\s+of\s+(?:Company|Entity|Applicant)",
    ],
    "uen": [r"^UEN"],
    "chart_date": [
        r"^As\s+at",
        r"^Chart\s+(?:as\s+at|Date)",
        r"^Information\s+as\s+at",
        r"^Prepared\s+on",
        r"^Date",
    ],
}

PCT_WORD = re.compile(r"^\(?(\d{1,3}(?:\.\d{1,4})?)\s*%\)?[.,;]?$")
BARE_PCT = re.compile(r"^\d{1,3}\.\d{2}$")
NUM_WORD = re.compile(r"^\(?(\d{1,3}(?:\.\d{1,4})?)\)?[.,;]?$")
UEN_SHAPE = re.compile(r"^(?:\d{8,9}[A-Z]|[TSR]\d{2}[A-Z]{2}\d{4}[A-Z])$")
NRIC_SHAPE = re.compile(r"^[STFGM]\d{7}[A-Z]$")
ID_LABEL_WORDS = {"id", "nric", "fin", "uen", "nric/fin", "no", "no.", "number", "id/uen"}
UBO_WORD = re.compile(r"^[\(\[]?UBO[\)\]:,;]*$", re.I)
UBO_LINE = re.compile(
    r"^(?:ubo|ultimate\s+beneficial\s+owner|beneficial\s+owner)(?:\s*[:=-]\s*(?:yes|true))?$", re.I
)
UBO_NEG_LINE = re.compile(r"^(?:not\s+(?:a\s+)?ubo|non[-\s]?ubo|ubo\s*[:=-]\s*(?:no|false))$", re.I)
TYPE_MARKERS = {
    "individual": "individual",
    "person": "individual",
    "natural person": "individual",
    "company": "company",
    "corporate": "company",
    "corporation": "company",
    "entity": "company",
    "legal entity": "company",
    "corporate entity": "company",
}
COMPANY_WORD = re.compile(
    r"\b(?:Pte|Ltd|Limited|LLP|Inc|Corp|Corporation|Bhd|Holdings|Trust|Nominees?)\b\.?", re.I
)
NAME_TAIL = re.compile(r"^(?:Pte\.?\s*Ltd\.?|Ltd\.?|Limited|LLP|Inc\.?|Bhd\.?|Holdings.*)$", re.I)
META_SEG = re.compile(
    r"threshold|^(?:as\s+at|chart\s+(?:as\s+at|date)|information\s+as\s+at|date)\b\s*[:\-]?"
    r"|^percentages?\b"
    r"|^(?:notes?|legend|key|source|prepared|generated)\s*[:\-]|^(?:prepared|generated)\s+(?:on|by)\b"
    r"|^arrows?\b"
    r"|ownership.*(?:chart|structure|diagram)"
    r"|^(?:shareholding|beneficial\s+ownership)\s+(?:chart|structure)",
    re.I,
)
PARENT_LINE = re.compile(r"(?:held\s+(?:in|via|through|by)|via|through|under)\b", re.I)
THRESHOLD_SEG = re.compile(r"threshold", re.I)
MAX_ITEMS = 300
LINK_FACTOR = 2.2  # lines closer than this many line heights belong to one box
PROSE_WORDS = 7  # a text run of this many words is a note or subtitle, never a holder's box


@dataclass
class Item:
    page: int
    words: list[Word]
    x0: float
    x1: float
    y0: float
    y1: float
    cy: float
    h: float


@dataclass
class Node:
    items: list[Item]
    page: int = 0
    x0: float = 0.0
    x1: float = 0.0
    y0: float = 0.0
    y1: float = 0.0
    pcts: int = 0
    has_alpha: bool = False

    def refresh(self) -> None:
        self.page = self.items[0].page
        self.x0 = min(i.x0 for i in self.items)
        self.x1 = max(i.x1 for i in self.items)
        self.y0 = min(i.y0 for i in self.items)
        self.y1 = max(i.y1 for i in self.items)
        self.pcts = sum(1 for i in self.items if _pct_in(i.words) is not None)
        self.has_alpha = any(re.search(r"[A-Za-z]", w.text) for i in self.items for w in i.words)

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2


@dataclass
class Parsed:
    node: Node
    name_words: list[Word] = field(default_factory=list)
    id_words: list[Word] = field(default_factory=list)
    id_raw: str | None = None
    type_value: str | None = None
    type_words: list[Word] = field(default_factory=list)
    pct_words: list[Word] = field(default_factory=list)
    pct_raw: str | None = None
    ubo_value: str | None = None
    ubo_words: list[Word] = field(default_factory=list)
    parent_words: list[Word] = field(default_factory=list)


def _pct_in(words: list[Word]) -> tuple[list[Word], str] | None:
    for i, w in enumerate(words):
        t = w.text.strip()
        m = PCT_WORD.match(t)
        if m:
            return [w], m.group(1) + "%"
        n = NUM_WORD.match(t)
        if n and i + 1 < len(words) and words[i + 1].text.strip().startswith("%"):
            return [w, words[i + 1]], n.group(1) + "%"
    if len(words) == 1 and BARE_PCT.match(words[0].text.strip()) and float(words[0].text) <= 100:
        return [words[0]], words[0].text.strip()
    return None


def _strip(t: str) -> str:
    return t.strip().strip("()[],;:")


def _item(seg: Seg, page: int) -> Item | None:
    ws = [w for w in seg.words if w.text.strip()]
    if not ws:
        return None
    return Item(
        page=page,
        words=ws,
        x0=min(w.x for w in ws),
        x1=max(w.x + w.w for w in ws),
        y0=min(w.y for w in ws),
        y1=max(w.y + w.h for w in ws),
        cy=median(w.y + w.h / 2 for w in ws),
        h=median(w.h for w in ws),
    )


def _build_nodes(view: DocView) -> list[Node]:
    items: list[Item] = []
    for pv in view.pages:
        for ln in pv.lines:
            for seg in ln.segs:
                text = seg.text
                if (
                    META_SEG.search(text)
                    or (len(seg.words) >= PROSE_WORDS and not PARENT_LINE.match(text))
                    or FOOTER.search(text)
                    or (ln.page, ln.index) in view.claimed
                ):
                    continue
                it = _item(seg, pv.page)
                if it is not None:
                    items.append(it)
    if len(items) > MAX_ITEMS:
        raise LayoutNotRecognised("too many text items for an ownership chart")
    items.sort(key=lambda i: (i.page, i.cy, i.x0))
    parent = list(range(len(items)))
    has_pct = [_pct_in(i.words) is not None for i in items]

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    pct_count = [1 if p else 0 for p in has_pct]
    for i, a in enumerate(items):
        for j in range(i + 1, len(items)):
            b = items[j]
            if b.page != a.page:
                break
            dy = b.cy - a.cy
            if dy > LINK_FACTOR * max(a.h, b.h):
                break
            if dy <= 0.25 * min(a.h, b.h):  # same line: joined later, never here
                continue
            overlap = min(a.x1, b.x1) - max(a.x0, b.x0)
            if overlap <= 0 and abs((a.x0 + a.x1) / 2 - (b.x0 + b.x1) / 2) >= 0.05:
                continue
            ra, rb = find(i), find(j)
            if ra == rb or (pct_count[ra] and pct_count[rb]):
                continue
            parent[rb] = ra
            pct_count[ra] += pct_count[rb]
    groups: dict[int, list[Item]] = {}
    for i, it in enumerate(items):
        groups.setdefault(find(i), []).append(it)
    nodes = [Node(items=g) for g in groups.values()]
    for n in nodes:
        n.refresh()
    return _join_same_line(nodes)


_MARKER_ONLY = re.compile(UBO_LINE.pattern + "|" + UBO_NEG_LINE.pattern, re.I)


def _satellite_kind(n: Node) -> str | None:
    if not n.has_alpha and n.pcts == 1:
        return "pct"
    text = " ".join(_strip(w.text) for i in n.items for w in i.words).strip()
    return "marker" if text and _MARKER_ONLY.match(text) else None


def _join_same_line(nodes: list[Node]) -> list[Node]:
    """Text drawn beside a box's other lines joins that box: a node holding only a percentage joins
    the name-only node whose text is nearest to its left on the same line; a lone ``UBO`` badge joins
    the node of the nearest text to its left on its line (the percentage it sits next to)."""
    out = list(nodes)
    for n in nodes:
        kind = _satellite_kind(n)
        if kind is None:
            continue
        best: tuple[Node, float] | None = None
        for m in out:
            if m is n or m.page != n.page:
                continue
            if kind == "pct" and (m.pcts or not m.has_alpha):
                continue
            for j in m.items:
                for i in n.items:
                    near = (
                        j.x1 <= i.x0 + 1e-6
                        and i.x0 - j.x1 <= 0.6
                        and abs(i.cy - j.cy) <= 0.6 * max(i.h, j.h)
                    )
                    if near and (best is None or j.x1 > best[1]):
                        best = (m, j.x1)
        if best is None:
            continue
        best[0].items.extend(n.items)
        best[0].refresh()
        out.remove(n)
    return out


def _parent_after(ws: list[Word]) -> list[Word] | None:
    """The words naming a parent after ``Held in`` / ``Held via`` / ``via`` / ``through`` / ``under``."""
    low = [_strip(w.text).casefold() for w in ws]
    if len(low) > 2 and low[0] == "held" and low[1] in ("in", "via", "through", "by"):
        return ws[2:]
    if len(low) > 1 and low[0] in ("via", "through", "under"):
        return ws[1:]
    return None


def _node_lines(node: Node) -> list[list[Word]]:
    """Items of a node regrouped into visual lines (labels in a small font sit on the same line as
    their value although their centres differ by a point)."""
    groups: list[tuple[float, float, list[Item]]] = []
    for it in sorted(node.items, key=lambda i: i.cy):
        if groups and abs(it.cy - groups[-1][0]) <= 0.6 * max(it.h, groups[-1][1]):
            cy, h, members = groups[-1]
            members.append(it)
            groups[-1] = (sum(m.cy for m in members) / len(members), max(h, it.h), members)
        else:
            groups.append((it.cy, it.h, [it]))
    return [
        [w for it in sorted(members, key=lambda i: i.x0) for w in it.words]
        for _cy, _h, members in groups
    ]


def _parse_node(node: Node) -> Parsed:
    """Read one box. ``name_words`` is empty when the box has no name line (an ID-only fragment)."""
    p = Parsed(node)
    leftovers: list[list[Word]] = []
    for ws in _node_lines(node):
        # percentage
        if p.pct_raw is None:
            found = _pct_in(ws)
            if found is not None:
                p.pct_words, p.pct_raw = found
        drop = {id(w) for w in p.pct_words}
        ws = [w for w in ws if id(w) not in drop]
        # UBO tag (whole line or an inline word such as "(UBO)")
        text = " ".join(_strip(w.text) for w in ws).strip()
        if text and UBO_NEG_LINE.match(text):
            p.ubo_value, p.ubo_words = "false", ws
            continue
        if text and UBO_LINE.match(text):
            p.ubo_value, p.ubo_words = "true", ws
            continue
        inline = [w for w in ws if UBO_WORD.match(w.text.strip())]
        if inline:
            p.ubo_value, p.ubo_words = "true", inline
            ws = [w for w in ws if w not in inline]
        # ID with an optional label before it
        for k, w in enumerate(ws):
            bare = _strip(w.text)
            if NRIC_SHAPE.match(bare) or UEN_SHAPE.match(bare):
                p.id_words, p.id_raw = [w], bare
                cut = {id(w)}
                if k > 0 and _strip(ws[k - 1].text).lower().rstrip(":") in ID_LABEL_WORDS:
                    cut.add(id(ws[k - 1]))
                    if k > 1 and _strip(ws[k - 2].text).lower().rstrip(":") in ID_LABEL_WORDS:
                        cut.add(id(ws[k - 2]))
                ws = [x for x in ws if id(x) not in cut]
                break
        if not ws:
            continue
        text = " ".join(_strip(w.text) for w in ws).strip()
        if text.casefold().rstrip(":") in ID_LABEL_WORDS:  # a lone "UEN" / "ID/UEN" label
            continue
        # printed holder type
        if text.casefold() in TYPE_MARKERS and p.type_value is None:
            p.type_value, p.type_words = TYPE_MARKERS[text.casefold()], ws
            continue
        parent = _parent_after(ws)
        if parent:
            p.parent_words = parent
        elif re.search(r"[A-Za-z]", text):
            leftovers.append([w for w in ws if re.search(r"[A-Za-z0-9]", w.text)])
    if leftovers:
        p.name_words = list(leftovers[0])
        if len(leftovers) > 1 and NAME_TAIL.match(words_text(leftovers[1]).strip()):
            p.name_words += leftovers[1]
    return p


def _ubo_cell(view: DocView, p: Parsed, uses_ubo: bool) -> Cell:
    if p.ubo_value is None:
        if uses_ubo:  # the chart uses UBO tags (a badge or a legend) and this box carries none
            return Cell(value="false", kind="bool", source="derived", verified=None)
        return Cell(value=None, kind="bool", source="text_layer")
    return Cell(
        value=p.ubo_value,
        raw=words_text(p.ubo_words)[:500],
        kind="bool",
        box=view.region(p.ubo_words),
        source="text_layer",
        verified=True,
    )


def _type_cell(view: DocView, p: Parsed, name: str) -> Cell:
    if p.type_value is not None:
        return Cell(
            value=p.type_value,
            raw=words_text(p.type_words)[:500],
            kind="enum",
            box=view.region(p.type_words),
            source="text_layer",
            verified=True,
        )
    derived: str | None = None
    if p.id_raw and NRIC_SHAPE.match(p.id_raw):
        derived = "individual"
    elif (p.id_raw and UEN_SHAPE.match(p.id_raw)) or COMPANY_WORD.search(name):
        derived = "company"
    return Cell(value=derived, kind="enum", source="derived" if derived else "text_layer")


@register_extractor(doc_types={DOC_TYPE}, backend="text_layer")
class OwnershipChartTextLayer(TextLayerExtractor):
    doc_types = frozenset({DOC_TYPE})
    name = "text_layer"
    version = "1"

    def parse(self, view: DocView, fields: dict[str, Cell], tables: dict[str, Table]) -> None:
        form = Form(view, DOC_TYPE, LABELS)
        name_hit = form.first("company_name")
        uen_hit = form.first("uen")  # the topmost UEN is the applicant's; holders print their own
        if uen_hit is not None:
            fields["uen"] = view.cell(uen_hit.value_words[:1], "id", "fields.uen")
        form.put(fields, "chart_date")
        self._threshold(view, fields)

        nodes = _build_nodes(view)
        parsed = [_parse_node(n) for n in nodes]
        parsed.sort(key=lambda p: (p.node.page, p.node.y0, p.node.x0))
        if not any(p.name_words for p in parsed):
            raise LayoutNotRecognised("no text nodes found on the ownership chart")

        # the applicant: the node holding the company-name label, else the topmost box that prints a
        # UEN and no percentage (titles and subtitles sit above it), else the topmost named box
        applicant: Parsed | None = None
        if name_hit is not None:
            key = (name_hit.label_words[0].page, round(name_hit.label_words[0].y, 5))
            applicant = next(
                (
                    p
                    for p in parsed
                    if any((w.page, round(w.y, 5)) == key for i in p.node.items for w in i.words)
                ),
                None,
            )
        applicant = (
            applicant
            or next(
                (p for p in parsed if p.id_raw and UEN_SHAPE.match(p.id_raw) and not p.pct_raw),
                None,
            )
            or next(p for p in parsed if p.name_words)
        )
        holders = [
            p
            for p in parsed
            if p is not applicant
            and p.name_words
            and (p.node.page, p.node.y0) > (applicant.node.page, applicant.node.y0)
        ]
        self._applicant_fields(view, fields, applicant, name_hit)
        if not holders:
            raise LayoutNotRecognised("no holders found below the applicant")

        tol = max(0.005, 0.8 * median(w.h for pv in view.pages for w in pv.words))
        pages, placed = self._rows(view, tables["holders"], applicant, holders, tol)
        if not placed:
            raise LayoutNotRecognised("no holders found below the applicant")
        lacking = sum(1 for p in placed if p.pct_raw is None)
        if lacking * 4 > len(placed):
            raise LayoutNotRecognised(
                f"{lacking} of {len(placed)} holders have no readable percentage"
            )
        view.row_pages["holders"] = pages

    # ------------------------------------------------------------------ fields
    @staticmethod
    def _applicant_fields(view: DocView, fields: dict[str, Cell], app: Parsed, name_hit) -> None:  # noqa: ANN001
        if name_hit is not None:
            fields["company_name"] = view.cell(name_hit.value_words, "text", "fields.company_name")
        elif app.name_words:
            fields["company_name"] = view.cell(app.name_words, "text", "fields.company_name")
        if fields["uen"].raw is None and app.id_raw and UEN_SHAPE.match(app.id_raw):
            fields["uen"] = view.cell(app.id_words, "id", "fields.uen", raw_override=app.id_raw)

    @staticmethod
    def _threshold(view: DocView, fields: dict[str, Cell]) -> None:
        for ln in view.all_lines:
            for seg in ln.segs:
                if not THRESHOLD_SEG.search(seg.text):
                    continue
                words = seg.words
                start = next(
                    (k for k, w in enumerate(words) if THRESHOLD_SEG.search(w.text)), len(words)
                )
                tail = words[start + 1 :]
                for k, w in enumerate(tail):
                    t = w.text.strip()
                    m = PCT_WORD.match(t) or NUM_WORD.match(t)
                    if not m:
                        continue
                    use = [w]
                    if (
                        not t.rstrip(".,;)").endswith("%")
                        and k + 1 < len(tail)
                        and tail[k + 1].text.strip().startswith("%")
                    ):
                        use.append(tail[k + 1])
                    raw = m.group(1) + ("%" if "%" in t or len(use) == 2 else "")
                    fields["ubo_threshold_percent"] = view.cell(
                        use, "percent", "fields.ubo_threshold_percent", raw_override=raw
                    )
                    return

    # ------------------------------------------------------------------ rows
    def _rows(
        self, view: DocView, table: Table, applicant: Parsed, holders: list[Parsed], tol: float
    ) -> tuple[list[int], list[Parsed]]:
        # tiers by top edge, applicant's tier first
        ordered = sorted([applicant, *holders], key=lambda p: (p.node.page, p.node.y0, p.node.x0))
        tiers: list[list[Parsed]] = []
        for p in ordered:
            last = tiers[-1] if tiers else None
            if (
                last
                and last[0].node.page == p.node.page
                and abs(p.node.y0 - last[0].node.y0) <= tol
            ):
                last.append(p)
            else:
                tiers.append([p])
        tier_of = {id(p): t for t, members in enumerate(tiers) for p in members}
        base = tier_of[id(applicant)]
        names: dict[int, Cell] = {}
        types: dict[int, Cell] = {}
        for p in holders:
            names[id(p)] = view.cell(p.name_words, "text", f"holders.{len(names)}.holder_name")
            types[id(p)] = _type_cell(view, p, names[id(p)].value or "")

        uses_ubo = any(
            re.search(r"\bUBO\b|beneficial\s+owner", ln.text, re.I) for ln in view.all_lines
        )
        pages: list[int] = []
        seq = sorted(
            (p for p in holders if tier_of[id(p)] > base),
            key=lambda p: (tier_of[id(p)], p.node.cx),
        )
        for i, p in enumerate(seq):
            where = f"holders[{i}]"
            row: Row = blank_row(DOC_TYPE, "holders", i)
            row.cells["holder_name"] = names[id(p)]
            row.cells["holder_type"] = types[id(p)]
            if p.id_raw:
                row.cells["id_or_uen"] = view.cell(
                    p.id_words, "id", f"{where}.id_or_uen", raw_override=p.id_raw
                )
            if p.pct_raw:
                row.cells["percent"] = view.cell(
                    p.pct_words, "percent", f"{where}.percent", raw_override=p.pct_raw
                )
            row.cells["parent_name"] = self._parent(
                view, p, seq, tier_of, base, names, types, where
            )
            row.cells["is_ubo"] = _ubo_cell(view, p, uses_ubo)
            row.box = row_box(row.cells.values()) or self._node_box(view, p)
            table.rows.append(row)
            pages.append(p.node.page)
        table.pages = sorted(set(pages))
        return pages, seq

    @staticmethod
    def _node_box(view: DocView, p: Parsed) -> Region | None:
        return view.region([w for i in p.node.items for w in i.words])

    @staticmethod
    def _parent(
        view: DocView,
        p: Parsed,
        seq: list[Parsed],
        tier_of: dict[int, int],
        base: int,
        names: dict[int, Cell],
        types: dict[int, Cell],
        where: str,
    ) -> Cell:
        if p.parent_words:
            return view.cell(p.parent_words, "text", f"{where}.parent_name")
        tier = tier_of[id(p)]
        if tier <= base + 1:
            return Cell(value=None, kind="text", source="text_layer")  # holds directly
        for t in range(tier - 1, base, -1):
            cands = [q for q in seq if tier_of[id(q)] == t and types[id(q)].value != "individual"]
            if cands:
                parent = min(cands, key=lambda q: abs(q.node.cx - p.node.cx))
                src = names[id(parent)]
                return Cell(
                    value=src.value,
                    kind="text",
                    box=src.box,
                    source="derived",
                    verified=None,
                )
        view.warn(f"chart_parent_unresolved: {names[id(p)].value}")
        return Cell(value=None, kind="text", source="text_layer")


make_validator(DOC_TYPE)

register_prompt_hints(
    DOC_TYPE,
    "This is a shareholding / beneficial ownership chart: boxes (people and companies) joined by "
    "arrows under the applicant company. `company_name` and `uen` are the applicant at the top; "
    "`ubo_threshold_percent` the threshold the chart prints (for example `25.00`). `holders` has one "
    "row per box below the applicant: `holder_name`, `holder_type` (individual or company), "
    "`id_or_uen` if printed, `percent` exactly as printed on that box, and `is_ubo` only if a "
    "UBO tag is printed on it. Use `parent_name` only for a box drawn under another holder's box: "
    "it is that holder's name, and the box's `percent` is the percentage held IN that parent, "
    "exactly as printed (never multiply it out). A box directly under the applicant has an empty "
    "`parent_name`. If a box prints no percentage, return null for it; do not estimate. Do not "
    "invent holders so that percentages add up to 100.",
)
