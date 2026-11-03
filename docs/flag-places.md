# Does each flag mark every place its evidence quotes?

A flag marks a main place and any related places (see [frontend.md](frontend.md) section 6.1, "Places"). When a flag's evidence quotes a figure that is printed on the flagged documents but lies inside none of its places, the viewer cannot show it: the audience reads a number in the evidence and sees no box for it. This table comes from `uv run python backend/scripts/audit_flag_places.py --out docs/flag-places.md`, which runs every red-team attack, the sample scenarios and the showcase documents through the pipeline and, for each flag, takes the money figures in its evidence and looks for them among the printed words of the flag's documents. Figures that are only computed (an expected balance) are printed nowhere and are ignored.

**Limits.** Only checks that fired in those runs are in the table (24 of the 69 registered checks), so a check that is not listed has not been audited, not cleared. A figure can also coincide with an unrelated printed number (a computed difference that happens to equal a printed amount), so a "gap" is a lead to read, not a proof.

**Fixed after the first run:** `bs_totals` now marks the cell that could not be read as one amount, besides the opening and closing balance, and `bs_running_balance` marks that cell too; `inv_duplicate` now marks the original invoice's total as well as its number.

**Still open (read the example before acting):** `x_invoice_vs_contract` quotes an invoice subtotal it does not mark; `pdf_modified` quotes figures from an earlier revision of the file, which is evidence about the file and has no single place; the `bs_running_balance` and `x_sof_support` examples are computed figures that coincide with printed ones.

| Check | Flags | Quoting a figure | Flags with a printed figure not marked | Example |
|---|---|---|---|---|
| `ai_provenance_hint` | 1 | 0 | 0 |  |
| `bs_benford` | 60 | 60 | 0 |  |
| `bs_holder` | 3 | 0 | 0 |  |
| `bs_running_balance` | 2 | 1 | 1 | 5000.00 in: previous 80215.34 - debit 0.35 = 80214.99, printed 85214.99; diff 5000.00; 1 later row(s) also break |
| `doc_instruction_text` | 3 | 0 | 0 |  |
| `doc_unclassified` | 109 | 109 | 0 |  |
| `doc_unreadable` | 9 | 0 | 0 |  |
| `font_mix` | 1 | 0 | 0 |  |
| `font_span_outlier` | 1 | 0 | 0 |  |
| `inv_duplicate` | 2 | 2 | 0 |  |
| `inv_sequence` | 12 | 0 | 0 |  |
| `inv_total` | 2 | 2 | 0 |  |
| `pdf_bytes_after_signature` | 1 | 1 | 0 |  |
| `pdf_modified` | 17 | 6 | 6 | 100928.18, 101807.49, 116978.64, 119094.51 in: text saved in revision 1 is not in the final file (page 1: '37,406.45', '37,406.95', '39,906.94', '49,048.14'  |
| `pdf_producer_unexpected` | 5 | 5 | 0 |  |
| `pdf_signature_invalid` | 1 | 0 | 0 |  |
| `pdf_text_vs_render` | 15 | 4 | 0 |  |
| `signature_reuse` | 4 | 1 | 0 |  |
| `x_cash_at_year_end` | 2 | 2 | 0 |  |
| `x_directors` | 2 | 0 | 0 |  |
| `x_invoice_vs_contract` | 3 | 3 | 2 | 73848.36 in: invoice INV-2025-0101 subtotal 73848.36 / total 80494.71 exceeds PO PO-ATP-6654 subtotal 24616.12 / total 2683 |
| `x_invoice_vs_receipts` | 8 | 7 | 0 |  |
| `x_ownership_vs_registry` | 13 | 12 | 0 |  |
| `x_sof_support` | 2 | 2 | 2 | 7.45 in: declared 70509.09 (sales_proceeds, 2026-07-20..2026-07-31); 1 supporting credit total 54237.76; unsupported 16 |
