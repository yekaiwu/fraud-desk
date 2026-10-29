"""Honest quirks Q01-Q14 and hard quirks H1-H3 (docs 4.1). Clean controls contain these; a rule
that fires on them is a measured false alarm."""

from __future__ import annotations

from .schema import Quirk

QUIRK_CATALOG: dict[str, Quirk] = {
    "Q01": Quirk(
        id="Q01",
        description="Bank fees and interest: account service charge 15.00, FAST/GIRO fees 0.15-0.50, tiny interest credit",
    ),
    "Q02": Quirk(
        id="Q02",
        description="Invoice layout B: per-line GST differs from net-total GST by up to 0.05",
    ),
    "Q03": Quirk(id="Q03", description="Payment lag 5-40 days after the invoice date"),
    "Q04": Quirk(id="Q04", description="One invoice paid in two instalments summing to the total"),
    "Q05": Quirk(
        id="Q05",
        description="Non-trade credits >= 5% of credits: director loan injection, supplier refund, financing proceeds",
    ),
    "Q06": Quirk(
        id="Q06",
        description="FY2024 financial statement printed in S$'000 (scale_printed=1000): subtotals off by up to 1,000; FY2025 prior column off by up to 500",
    ),
    "Q07": Quirk(
        id="Q07", description="One month ends in overdraft (negative balance during the month)"
    ),
    "Q08": Quirk(
        id="Q08",
        description="Recurring identical rows on different dates (rent, service charge) and a fixed monthly round-number retainer credit",
        may_trigger=[],
    ),
    "Q09": Quirk(
        id="Q09",
        description="ocbc_style statements carry a placeholder signature with several %%EOF; ByteRange reaches end of file",
    ),
    "Q10": Quirk(id="Q10", description="Each layout carries its own producer string"),
    "Q11": Quirk(
        id="Q11",
        description="Name surface forms differ between registry and documents (case, title, patronymic abbreviation)",
    ),
    "Q12": Quirk(
        id="Q12",
        description="Boilerplate pages: T&C page after each ocbc_style statement, one blank page in one invoice PDF",
        may_trigger=["doc_unclassified"],
    ),
    "Q14": Quirk(
        id="Q14",
        description="Management accounts: FY2025 printed as Management Accounts (Unaudited), basis unaudited, no auditor",
    ),
    "H1": Quirk(
        id="H1",
        description="One credit pays two invoices",
        may_trigger=["x_invoice_vs_receipts"],
        hard=True,
    ),
    "H2": Quirk(
        id="H2",
        description="Two identical same-day same-amount payments to the same counterparty",
        may_trigger=["bs_duplicates"],
        hard=True,
    ),
    "H3": Quirk(
        id="H3",
        description="One invoice number omitted from the sequence (voided invoice)",
        may_trigger=["inv_sequence"],
        hard=True,
    ),
}


def default_quirks(company_index: int) -> list[str]:
    """Q01..Q12, plus Q14 on odd company indexes."""
    q = [f"Q{i:02d}" for i in range(1, 13)]
    if company_index % 2 == 1:
        q.append("Q14")
    return q


def hard_quirks_for(scenario: str) -> list[str]:
    """Hard quirks of the ``--quirk-hard`` bundle of a scenario (company indexes 90 to 92)."""
    return {"loan": ["H1", "H2", "H3"], "kyc": ["H2"], "aml": ["H1", "H2", "H3"]}[scenario]


def records(ids: list[str]) -> list[Quirk]:
    return [QUIRK_CATALOG[i] for i in ids if i in QUIRK_CATALOG]
