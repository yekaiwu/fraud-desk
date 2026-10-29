"""Plain dataclasses of the generator's World (docs 4.1). Everything that is *printed* is stored
as printed (Decimal / date / str) so that tamper mutations can make the page disagree with the
arithmetic; helper functions recompute on request."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any


@dataclass
class Party:
    name: str  # proper case, "Acme Trading Pte. Ltd."
    uen: str
    gst_no: str | None = None
    address: str = ""


@dataclass
class Person:
    key: str  # "p0"
    name: str  # printed form on profile and ID
    group: str
    sex: str  # M | F
    birth: date
    id_number: str
    nationality: str = "SG"
    id_kind: str = "nric"  # nric | passport
    position: str = "Director"
    appointment: date = date(2019, 1, 1)
    registry_name: str = ""
    address: str = ""
    issue_date: date = date(2015, 1, 1)
    is_director: bool = True
    on_profile: bool = True  # listed as a director on the company profile
    in_registry: bool = True  # a registry officer
    surname: str = ""
    given_names: str = ""
    passport_expiry: date | None = None
    sig_seed: int = 0


@dataclass
class Holder:
    name: str  # printed
    holder_type: str  # individual | company
    id_or_uen: str | None
    shares: int
    percent: Decimal
    registry_name: str = ""
    person_key: str | None = None
    share_class: str = "Ordinary"
    parent: str | None = None  # intermediate holder (chart percent is held in the parent)
    is_ubo: bool = False


@dataclass
class LedgerRow:
    date: date
    description: str
    debit: Decimal | None
    credit: Decimal | None
    balance: Decimal
    c4: str | None = None
    c5: str = "other"
    counterparty: str | None = None
    ref: str | None = None  # "invoice:INV-2025-0105" for rows that pay an invoice
    kind: str = "misc"  # generator-internal tag (trade, retainer, fee, payroll, ...)


@dataclass
class Statement:
    month: str  # "2025-07"
    account_number: str
    account_holder: str  # printed (upper case)
    uen: str | None
    bank_name: str | None
    period_start: date
    period_end: date
    opening: Decimal
    closing: Decimal
    total_debits: Decimal
    total_credits: Decimal
    rows: list[LedgerRow] = field(default_factory=list)
    layout: str = "dbs_style"
    dropped_pages: list[int] = field(default_factory=list)
    brought_forward_row: bool = False  # dbs_style prints a balance-only first row

    @property
    def key(self) -> str:
        return f"bank_statement:{self.month}"


@dataclass
class InvLine:
    description: str
    quantity: Decimal
    unit_price: Decimal
    amount: Decimal
    gst: Decimal | None = None  # per-line GST (layout B)


@dataclass
class Invoice:
    number: str
    date: date
    due: date
    supplier: Party
    buyer: Party
    lines: list[InvLine]
    subtotal: Decimal
    gst_rate: Decimal
    gst_amount: Decimal
    total: Decimal
    layout: str = "A"  # A: GST on net total, B: per-line GST
    po_ref: str | None = None
    contract_ref: str | None = None
    status: str | None = None  # paid | unpaid | partial | None (no stamp)
    paid_date: date | None = None
    blank_page: bool = False  # Q12: one blank trailing page
    currency: str = "SGD"
    producer: str | None = None

    @property
    def key(self) -> str:
        return f"invoice:{self.number}"


@dataclass
class FSLine:
    line: str
    label: str
    section: str
    current: Decimal | None
    prior: Decimal | None
    is_subtotal: bool = False


@dataclass
class FinStmt:
    fy: str  # "FY2025"
    period_start: date
    period_end: date
    company_name: str
    uen: str
    scale_printed: int = 1
    basis: str = "audited"
    auditor: str | None = "Harbourview Assurance LLP"
    tables: dict[str, list[FSLine]] = field(default_factory=dict)
    title: str = "Financial Statements"
    params: dict[str, Any] = field(default_factory=dict)  # generator bookkeeping (tax rate...)

    @property
    def key(self) -> str:
        return f"financial_statement:{self.fy}"


@dataclass
class Contract:
    n: int
    number: str
    title: str
    contract_date: date
    effective: date
    end: date
    seller: Party
    buyer: Party
    value: Decimal
    payment_terms: str
    signatory_seller: str
    signatory_buyer: str
    sig_seller_seed: int
    sig_buyer_seed: int
    sig_reuse_from: str | None = None  # doc_key whose signature images are reused pixel-identical

    @property
    def key(self) -> str:
        return f"contract:{self.n}"


@dataclass
class PurchaseOrder:
    n: int
    number: str
    date: date
    buyer: Party
    supplier: Party
    lines: list[InvLine]
    subtotal: Decimal
    gst_amount: Decimal
    total: Decimal
    delivery: date
    contract_ref: str | None = None

    @property
    def key(self) -> str:
        return f"purchase_order:{self.n}"


@dataclass
class Customer:
    party: Party
    label: str  # C5 label of the business
    major: bool = False
    contract_n: int | None = None
    counterparty: str = ""  # upper-case statement form


@dataclass
class Company:
    party: Party
    entity_type: str = "Private Company Limited by Shares"
    status: str = "Live"
    incorporation: date = date(2019, 4, 12)
    activity: str = ""
    ssic: str = ""
    paid_up_capital: Decimal = Decimal("300000.00")
    currency: str = "SGD"
    bank_name: str = ""
    account_number: str = ""
