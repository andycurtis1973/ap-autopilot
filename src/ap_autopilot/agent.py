"""The AP Autopilot agent (Strands). Given an extracted invoice, it reasons
through the accounts-payable workflow by calling tools:

  lookup_purchase_order → lookup_goods_receipt → validate_and_match → then
  post_to_erp (clean) or route_for_approval (exception)

The deterministic controls live in ``validate_and_match`` (the agent never does
arithmetic itself — in the deployed system that tool runs in the AgentCore Code
Interpreter). The model's job is orchestration and the final post-vs-route call.
The same tool functions are exported for the AgentCore Gateway (deploy/).
"""

from __future__ import annotations

import json

from .model import Invoice, AUTO_APPROVED
from .pipeline import decide, SeenStore
from .tools import APBackend, po_from_dict, receipt_from_dict
from .bedrock_models import DEFAULT

SYSTEM_PROMPT = """You are AP Autopilot, an accounts-payable agent. You process one vendor invoice
at a time and decide whether it is safe to pay automatically.

Workflow for each invoice (the invoice JSON is given to you):
1. Call lookup_purchase_order with the invoice's po_number to retrieve the authorizing PO.
2. Call lookup_goods_receipt with the same po_number to confirm what was received.
3. Call validate_and_match with the invoice JSON. This runs the deterministic 3-way-match
   controls (math, price/quantity tolerances, duplicates) and returns the exceptions and a
   recommended_status. Trust its arithmetic — do not recompute totals yourself.
4. If recommended_status is "auto_approved", call post_to_erp to schedule payment.
   Otherwise call route_for_approval with a concise reason and the exception codes.
5. Reply with ONE sentence: the invoice id, the decision, and the key reason.

Never approve an invoice the controls flagged as a blocker. Be concise."""


def make_tools(backend: APBackend, seen: SeenStore):
    """Build the agent's tool set bound to a backend + duplicate-memory."""
    from strands import tool

    @tool
    def lookup_purchase_order(po_number: str) -> str:
        """Look up a purchase order by PO number. Returns the PO as JSON."""
        return json.dumps(backend.lookup_po(po_number))

    @tool
    def lookup_goods_receipt(po_number: str) -> str:
        """Look up the goods receipt for a PO number. Returns the receipt as JSON."""
        return json.dumps(backend.lookup_goods_receipt(po_number))

    @tool
    def validate_and_match(invoice_json: str) -> str:
        """Run the deterministic AP controls (math validation + 3-way match +
        duplicate check) on an invoice. Returns exceptions and a recommended
        status (auto_approved | routed_for_approval | rejected)."""
        inv = Invoice.from_dict(json.loads(invoice_json))
        po = po_from_dict(backend.lookup_po(inv.po_number)) if inv.po_number else None
        rcpt = receipt_from_dict(backend.lookup_goods_receipt(inv.po_number)) if inv.po_number else None
        d = decide(inv, po, rcpt, seen)
        return json.dumps({"recommended_status": d.status, "matched": d.matched,
                           "exceptions": [e.to_dict() for e in d.exceptions]})

    @tool
    def post_to_erp(invoice_id: str, vendor_id: str, amount: str) -> str:
        """Schedule an approved invoice for payment in the ERP."""
        return json.dumps(backend.post_to_erp(invoice_id, vendor_id, amount))

    @tool
    def route_for_approval(invoice_id: str, reason: str, exception_codes: str) -> str:
        """Open a human-review ticket for an invoice that failed a control."""
        codes = [c.strip() for c in exception_codes.split(",") if c.strip()]
        return json.dumps(backend.route_for_approval(invoice_id, reason, codes))

    return [lookup_purchase_order, lookup_goods_receipt, validate_and_match,
            post_to_erp, route_for_approval]


def build_agent(backend: APBackend, seen: SeenStore | None = None,
                model_id: str = DEFAULT.model_id, region: str = "us-east-1"):
    """Construct the Strands agent wired to real Bedrock + the AP tools."""
    from strands import Agent
    from strands.models import BedrockModel

    seen = seen if seen is not None else SeenStore()
    model = BedrockModel(model_id=model_id, region_name=region, temperature=0)
    return Agent(model=model, system_prompt=SYSTEM_PROMPT,
                 tools=make_tools(backend, seen), name="ap-autopilot")


def invoice_prompt(inv: Invoice) -> str:
    return ("Process this invoice:\n```json\n"
            + json.dumps(inv.to_dict(), indent=2) + "\n```")
