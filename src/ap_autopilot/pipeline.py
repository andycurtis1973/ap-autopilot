"""The invoice-to-pay decision pipeline: extract → validate → 3-way match →
decide. Pure orchestration over the deterministic pieces, so the same function
runs in the unit suite (ground-truth extractor) and inside the AgentCore Runtime
(Bedrock extractor + Gateway-backed PO/receipt lookups).

The agent (agent.py) is a thin reasoning wrapper over exactly these steps — it
chooses *which* tool to call; the controls themselves live here so they're
testable and auditable independent of any model.
"""

from __future__ import annotations

from .model import (Invoice, PurchaseOrder, GoodsReceipt, Decision, Exception_,
                    SEVERITY_BLOCKER, SEVERITY_REVIEW,
                    AUTO_APPROVED, ROUTED, REJECTED)
from .validate import validate
from .match import three_way_match, MatchPolicy, DEFAULT_POLICY


class SeenStore:
    """Tracks (vendor_id, invoice_id) already processed — duplicate-payment guard.

    In the live system this is AgentCore **Memory** (durable, per-vendor). Here
    it's an in-memory set so the offline pipeline and tests behave identically.
    """

    def __init__(self) -> None:
        self._seen: set[tuple[str, str]] = set()

    def is_duplicate(self, inv: Invoice) -> bool:
        return (inv.vendor_id, inv.invoice_id) in self._seen

    def record(self, inv: Invoice) -> None:
        self._seen.add((inv.vendor_id, inv.invoice_id))


def decide(inv: Invoice, po: PurchaseOrder | None, receipt: GoodsReceipt | None,
           seen: SeenStore | None = None,
           policy: MatchPolicy = DEFAULT_POLICY) -> Decision:
    """Run all controls and turn the exceptions into a verdict.

    Any BLOCKER → REJECTED (never pay). Else any REVIEW → ROUTED (human approves).
    Else AUTO_APPROVED — straight-through, post to ERP.
    """
    exceptions: list[Exception_] = []

    if seen is not None and seen.is_duplicate(inv):
        exceptions.append(Exception_(
            "DUPLICATE_INVOICE", SEVERITY_BLOCKER,
            f"Invoice {inv.invoice_id} from {inv.vendor_id} already processed",
            {"invoice_id": inv.invoice_id, "vendor_id": inv.vendor_id}))

    exceptions += validate(inv)
    exceptions += three_way_match(inv, po, receipt, policy)

    if any(e.severity == SEVERITY_BLOCKER for e in exceptions):
        status = REJECTED
    elif any(e.severity == SEVERITY_REVIEW for e in exceptions):
        status = ROUTED
    else:
        status = AUTO_APPROVED

    if seen is not None and status != REJECTED:
        seen.record(inv)

    return Decision(invoice_id=inv.invoice_id, status=status, exceptions=exceptions,
                    matched=(status == AUTO_APPROVED), extracted=inv)
