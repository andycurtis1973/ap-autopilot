"""The accounts-payable business tools the agent calls — the ERP/procurement
back end. These are plain functions over JSON-serializable dicts so the SAME
implementations run three ways without change:

  • locally, called directly by the Strands agent (scripts/run_agent.py)
  • behind an AgentCore **Gateway** as MCP tools (deploy/gateway_lambda.py wraps them)
  • inside the unit suite

The "ERP" is the generated records.json (POs + goods receipts) plus an in-memory
ledger of posted vouchers / raised approval tickets. Duplicate detection and
vendor history are delegated to a ``VendorMemory`` so the live system can swap in
AgentCore Memory without touching this logic.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol


class VendorMemory(Protocol):
    def is_duplicate(self, vendor_id: str, invoice_id: str) -> bool: ...
    def record_invoice(self, vendor_id: str, invoice_id: str) -> None: ...


class InMemoryVendorMemory:
    """Local stand-in for AgentCore Memory (per-vendor invoice history)."""

    def __init__(self) -> None:
        self._seen: set[tuple[str, str]] = set()

    def is_duplicate(self, vendor_id: str, invoice_id: str) -> bool:
        return (vendor_id, invoice_id) in self._seen

    def record_invoice(self, vendor_id: str, invoice_id: str) -> None:
        self._seen.add((vendor_id, invoice_id))


class APBackend:
    """Procurement + ERP back end over the generated records."""

    def __init__(self, records_path: str | Path = "data/records.json",
                 memory: VendorMemory | None = None):
        data = json.loads(Path(records_path).read_text())
        self._pos: dict = data.get("purchase_orders", {})
        self._receipts: dict = data.get("goods_receipts", {})
        self.memory = memory or InMemoryVendorMemory()
        self.ledger: list[dict] = []   # posted vouchers + routed tickets

    # --- the four MCP tools ------------------------------------------------
    def lookup_po(self, po_number: str) -> dict:
        """Return the purchase order for a PO number, or {'found': False}."""
        po = self._pos.get(po_number)
        return {"found": True, **po} if po else {"found": False, "po_number": po_number}

    def lookup_goods_receipt(self, po_number: str) -> dict:
        """Return the goods receipt for a PO number, or {'found': False}."""
        r = self._receipts.get(po_number)
        return {"found": True, **r} if r else {"found": False, "po_number": po_number}

    def post_to_erp(self, invoice_id: str, vendor_id: str, amount: str) -> dict:
        """Record an approved invoice as a payable voucher."""
        voucher = {"action": "post_to_erp", "voucher_id": f"VCH-{len(self.ledger)+1000}",
                   "invoice_id": invoice_id, "vendor_id": vendor_id, "amount": amount,
                   "status": "scheduled_for_payment"}
        self.ledger.append(voucher)
        return voucher

    def route_for_approval(self, invoice_id: str, reason: str, exceptions: list) -> dict:
        """Open a review ticket for an invoice that failed a control."""
        ticket = {"action": "route_for_approval", "ticket_id": f"TKT-{len(self.ledger)+5000}",
                  "invoice_id": invoice_id, "reason": reason, "exceptions": exceptions,
                  "status": "awaiting_human_review"}
        self.ledger.append(ticket)
        return ticket


# --- dict <-> model helpers (the Gateway speaks JSON; the controls speak models) ---
def po_from_dict(d: dict):
    from .model import PurchaseOrder, POLine
    if not d.get("found"):
        return None
    return PurchaseOrder(
        po_number=d["po_number"], vendor_id=d.get("vendor_id", ""),
        vendor_name=d.get("vendor_name", ""), currency=d.get("currency", "USD"),
        lines=[POLine(l["sku"], l.get("description", ""), l["quantity_ordered"],
                      l["unit_price"]) for l in d.get("lines", [])])


def receipt_from_dict(d: dict):
    from .model import GoodsReceipt, ReceiptLine
    if not d.get("found"):
        return None
    return GoodsReceipt(
        receipt_id=d.get("receipt_id", ""), po_number=d["po_number"],
        received_date=d.get("received_date", ""),
        lines=[ReceiptLine(l["sku"], l["quantity_received"]) for l in d.get("lines", [])])
