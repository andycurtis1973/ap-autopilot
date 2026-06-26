"""Three-way match: invoice ↔ purchase order ↔ goods receipt.

This is the heart of accounts-payable controls. An invoice is only safe to pay
when (1) it references a real PO for the same vendor, (2) what's billed matches
what was *ordered* (price/quantity within tolerance), and (3) what's billed was
actually *received*. Anything outside tolerance becomes an exception; the
severity decides auto-pay vs human review (see ``decide``).

Tolerances live in one ``MatchPolicy`` so "what counts as a price variance" is a
single reviewed knob, not scattered magic numbers. In the live system the policy
defaults can be overridden per-vendor from AgentCore **Memory** (a vendor with a
contracted price-protection clause gets a tighter band, say).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .model import (Invoice, PurchaseOrder, GoodsReceipt, Exception_,
                    SEVERITY_REVIEW, SEVERITY_BLOCKER, money, qty)


@dataclass(frozen=True)
class MatchPolicy:
    # A unit price above PO by more than this fraction OR this many dollars is a
    # variance (whichever is larger — small-dollar lines shouldn't trip on %).
    price_tolerance_pct: Decimal = Decimal("0.02")     # 2%
    price_tolerance_abs: Decimal = money("1.00")       # $1.00
    # Billing for more than received (over-billing) is never allowed past this.
    qty_tolerance_units: Decimal = qty("0")
    # Total-level guard so a pile of tiny in-tolerance line drifts can't add up.
    total_tolerance_pct: Decimal = Decimal("0.01")     # 1%
    total_tolerance_abs: Decimal = money("25.00")


DEFAULT_POLICY = MatchPolicy()


def _price_band(po_price: Decimal, p: MatchPolicy) -> Decimal:
    return max(money(po_price * p.price_tolerance_pct), p.price_tolerance_abs)


def three_way_match(inv: Invoice, po: PurchaseOrder | None,
                    receipt: GoodsReceipt | None,
                    policy: MatchPolicy = DEFAULT_POLICY) -> list[Exception_]:
    """Match an invoice against its PO and goods receipt. Returns exceptions."""
    out: list[Exception_] = []

    # (0) Must reference a PO that exists.
    if not inv.po_number:
        out.append(Exception_("NO_PO_REFERENCE", SEVERITY_REVIEW,
                              "Invoice does not reference a purchase order"))
        return out
    if po is None:
        out.append(Exception_("PO_NOT_FOUND", SEVERITY_BLOCKER,
                              f"Referenced PO {inv.po_number} was not found",
                              {"po_number": inv.po_number}))
        return out

    # (1) Vendor on the invoice must be the vendor on the PO.
    if inv.vendor_id and po.vendor_id and inv.vendor_id != po.vendor_id:
        out.append(Exception_("VENDOR_MISMATCH", SEVERITY_BLOCKER,
                              f"Invoice vendor {inv.vendor_id} != PO vendor {po.vendor_id}",
                              {"invoice_vendor": inv.vendor_id, "po_vendor": po.vendor_id}))

    # (2 & 3) Per-line price (vs PO) and quantity (vs received) checks.
    for li in inv.line_items:
        pol = po.line_by_sku(li.sku)
        if pol is None:
            out.append(Exception_("UNORDERED_ITEM", SEVERITY_REVIEW,
                                  f"Billed item '{li.sku}' is not on PO {po.po_number}",
                                  {"sku": li.sku}))
            continue

        band = _price_band(pol.unit_price, policy)
        if li.unit_price - pol.unit_price > band:
            out.append(Exception_("PRICE_VARIANCE", SEVERITY_REVIEW,
                                  f"'{li.sku}' billed {li.unit_price} vs PO {pol.unit_price} "
                                  f"(tol ±{band})",
                                  {"sku": li.sku, "billed": str(li.unit_price),
                                   "po": str(pol.unit_price), "tolerance": str(band)}))

        if li.quantity - pol.quantity_ordered > policy.qty_tolerance_units:
            out.append(Exception_("OVER_PO_QUANTITY", SEVERITY_REVIEW,
                                  f"'{li.sku}' billed qty {li.quantity} exceeds ordered "
                                  f"{pol.quantity_ordered}",
                                  {"sku": li.sku, "billed": str(li.quantity),
                                   "ordered": str(pol.quantity_ordered)}))

        received = receipt.received_by_sku(li.sku) if receipt else qty("0")
        if li.quantity - received > policy.qty_tolerance_units:
            sev = SEVERITY_BLOCKER if receipt is None else SEVERITY_REVIEW
            out.append(Exception_("QUANTITY_NOT_RECEIVED", sev,
                                  f"'{li.sku}' billed {li.quantity} but only {received} received",
                                  {"sku": li.sku, "billed": str(li.quantity),
                                   "received": str(received)}))

    # (4) Total-level guard against accumulated drift. Compare PRE-TAX amounts:
    # the PO total is pre-tax, so we match it against the invoice subtotal (tax
    # itself is checked in validate_math). Catches many small in-tolerance line
    # drifts that individually pass but collectively overrun the PO.
    band = max(money(po.total * policy.total_tolerance_pct), policy.total_tolerance_abs)
    if inv.subtotal - po.total > band:
        out.append(Exception_("SUBTOTAL_OVER_PO", SEVERITY_REVIEW,
                              f"Invoice subtotal {inv.subtotal} exceeds PO total {po.total} (tol ±{band})",
                              {"invoice_subtotal": str(inv.subtotal), "po_total": str(po.total),
                               "tolerance": str(band)}))

    return out
