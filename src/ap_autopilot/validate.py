"""Stateless validation of a single extracted invoice — the math and the
self-consistency checks that don't need the PO or receipt.

Everything here is deterministic Decimal arithmetic. In the live agent this same
arithmetic runs inside the AgentCore **Code Interpreter** (sandboxed) so the LLM
never does the math itself — it only decides what to check. Keeping the logic
here as a pure function means the unit suite and the Code Interpreter run
*identical* code.
"""

from __future__ import annotations

from decimal import Decimal

from .model import Invoice, Exception_, SEVERITY_REVIEW, SEVERITY_BLOCKER, money

# Pennies of slack allowed before we call it a math error (rounding on the
# vendor's side is normal; a real mistake is dollars, not cents).
CENT_TOLERANCE = money("0.02")


def validate_math(inv: Invoice) -> list[Exception_]:
    """Check the invoice is internally consistent: line extensions, subtotal,
    and subtotal + tax == total. Returns one Exception_ per failed check."""
    out: list[Exception_] = []

    for li in inv.line_items:
        if abs(li.amount - li.computed_amount) > CENT_TOLERANCE:
            out.append(Exception_(
                code="LINE_EXTENSION_ERROR", severity=SEVERITY_REVIEW,
                message=f"Line '{li.sku}' amount {li.amount} != qty×price {li.computed_amount}",
                detail={"sku": li.sku, "printed": str(li.amount),
                        "computed": str(li.computed_amount)}))

    if abs(inv.subtotal - inv.computed_subtotal) > CENT_TOLERANCE:
        out.append(Exception_(
            code="SUBTOTAL_ERROR", severity=SEVERITY_REVIEW,
            message=f"Subtotal {inv.subtotal} != sum of lines {inv.computed_subtotal}",
            detail={"printed": str(inv.subtotal), "computed": str(inv.computed_subtotal)}))

    computed_total = money(inv.subtotal + inv.tax)
    if abs(inv.total - computed_total) > CENT_TOLERANCE:
        out.append(Exception_(
            code="TOTAL_ERROR", severity=SEVERITY_REVIEW,
            message=f"Total {inv.total} != subtotal + tax {computed_total}",
            detail={"printed": str(inv.total), "computed": str(computed_total)}))

    return out


def validate_fields(inv: Invoice) -> list[Exception_]:
    """Structural sanity: required fields present, amounts non-negative."""
    out: list[Exception_] = []
    if not inv.invoice_id:
        out.append(Exception_("MISSING_INVOICE_ID", SEVERITY_BLOCKER,
                              "Invoice has no invoice number"))
    if not inv.line_items:
        out.append(Exception_("NO_LINE_ITEMS", SEVERITY_REVIEW,
                              "No line items were extracted"))
    if inv.total < Decimal("0"):
        out.append(Exception_("NEGATIVE_TOTAL", SEVERITY_REVIEW,
                              f"Invoice total is negative ({inv.total})"))
    return out


def validate(inv: Invoice) -> list[Exception_]:
    """All single-invoice checks."""
    return validate_fields(inv) + validate_math(inv)
