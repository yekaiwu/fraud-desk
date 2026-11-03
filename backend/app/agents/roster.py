"""Who owns which rule, and which flags the challenger may argue about.

The four specialists are the existing checks grouped by what they examine. A rule belongs to exactly
one of them, so a case's flags always have an owner in the trail.
"""

from __future__ import annotations

from app.core.models import AgentName
from app.semantic.policy import is_semantic_rule

ROLES: dict[AgentName, str] = {
    "documents": "Examines the files themselves: PDF structure, fonts, images, re-used files.",
    "finance": "Checks the money: statement arithmetic, invoices, financial statements, "
    "and how the figures line up across documents.",
    "identity": "Checks who is named: ID numbers, passports, company numbers, directors, owners.",
    "relationships": "Checks how parties and declarations connect: payers, sources of funds, "
    "stated business against activity, and model-based fit checks.",
    "challenger": "Argues the innocent reading of each judgement flag, and lowers it only when it "
    "can quote the documents' own explanation.",
    "case_officer": "Reads the whole trail and writes the summary and next steps for the reviewer.",
}

_DOCUMENTS = frozenset({"file_reuse", "doc_unclassified", "doc_unreadable"})
_IDENTITY = frozenset(
    {
        "id_checksum",
        "id_format",
        "passport_mrz",
        "kyc_shareholdings",
        "date_valid",
        "fs_entity",
        "inv_parties",
        "x_directors",
        "x_entity",
        "x_ownership_vs_registry",
        "x_sanctions_screen",
        "x_entity_vs_gleif",
        "x_address_vs_registry",
    }
)
_FINANCE_PREFIXES = ("bs_", "fs_", "inv_")
_FINANCE_CROSS = frozenset(
    {
        "x_cash_at_year_end",
        "x_revenue_vs_credits",
        "x_invoice_vs_contract",
        "x_invoice_vs_receipts",
        "sem_txn_labels",
        "sem_income_fit",
    }
)

# Flags about who or what a document refers to, or whether declarations fit: two honest readings of
# the same pages can disagree about them, so the challenger may argue them. Arithmetic flags are not
# on this list: a total that does not add up is not a matter of opinion.
ARGUABLE = frozenset(
    {
        "passport_mrz",
        "kyc_shareholdings",
        "bs_holder",
        "fs_entity",
        "inv_parties",
        "x_directors",
        "x_entity",
        "x_ownership_vs_registry",
        "x_remitter_vs_buyer",
        "x_sof_support",
        "x_declared_vs_activity",
    }
)


def owner(rule: str, stage: str) -> AgentName:
    if stage == "forensics" or rule in _DOCUMENTS:
        return "documents"
    if rule in _IDENTITY:
        return "identity"
    if rule in _FINANCE_CROSS or rule.startswith(_FINANCE_PREFIXES):
        return "finance"
    return "relationships"


def is_arguable(rule: str) -> bool:
    return rule in ARGUABLE or is_semantic_rule(rule)
